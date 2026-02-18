from time import sleep
from dataclasses import dataclass
from typing import Optional

import requests
import spacy
from bs4 import BeautifulSoup

WIKIPEDIA_BASE_URL = "https://en.wikipedia.org"
WIKIPEDIA_ARTICLE_PREFIX = "/wiki/"

REQUEST_DELAY_SECONDS = 5
DEFAULT_STEP_LIMIT = 10


@dataclass
class TraversalResult:
    success: bool
    path: list[str]
    steps_taken: int
    start_url: str
    target_url: str
    error: Optional[str] = None

    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        path_str = " -> ".join(url_to_title(url) for url in self.path)
        return f"[{status}] {self.steps_taken} steps\nPath: {path_str}"


nlp = spacy.load("en_core_web_lg")


def url_to_title(url: str) -> str:
    return url.rsplit("/", 1)[-1].replace("_", " ")


def is_valid_wiki_link(href: str) -> bool:
    if not href.startswith(WIKIPEDIA_ARTICLE_PREFIX):
        return False
    return ":" not in href.split("/wiki/", 1)[-1]


def extract_article_url(href: str) -> str:
    # Strip any in-page anchor (#Section)
    clean_href = href.split("#")[0]
    return WIKIPEDIA_BASE_URL + clean_href


def fetch_page(url: str, session: requests.Session) -> Optional[BeautifulSoup]:
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")
    except requests.RequestException as exc:
        print(exc)
        return None


def extract_article_links(soup: BeautifulSoup) -> list[str]:
    body = soup.find(id="bodyContent")
    if body is None:
        return []

    seen: set[str] = set()
    links: list[str] = []
    # Iterate through all links with an href (link to another article)
    for tag in body.find_all("a", href=True):
        href = tag.get("href", "")
        if not isinstance(href, str):
            continue
        if not is_valid_wiki_link(href):
            continue
        url = extract_article_url(href)
        # Filter out non-unique links
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def score_candidate(candidate_url: str, target_doc) -> int:
    title = url_to_title(candidate_url)
    similarity = target_doc.similarity(nlp(title))

    return similarity


def traverse_wiki(start_url: str, target_url: str, step_limit: int = DEFAULT_STEP_LIMIT) -> TraversalResult:
    # Parse the targetURL for the semantic meaning of the title
    target_title = url_to_title(target_url)
    target_doc = nlp(target_title)
    current_url = start_url
    # Path keeps track of the pages we ultimately visit
    path: list[str] = [current_url]

    session = requests.Session()
    session.headers.update({"User-Agent": "WikiTraversal/2.0 (education project)"})

    for step in range(step_limit):
        # 1. Determine if current URL is the target URL
        if current_url == target_url:
            return TraversalResult(
                success=True,
                path=path,
                steps_taken=step,
                start_url=start_url,
                target_url=target_url,
            )

        # 2. Go to valid URL
        soup = fetch_page(current_url, session)
        if soup is None:
            continue

        # 3a. Collect links
        links: list[str] = extract_article_links(soup)
        semantic_similarity = 0

        for link in links:
            # 3b. In order to prevent loops, I prevent the
            # program from looking at links to pages
            # we've already traversed
            if link in path:
                continue

            similarity_score: int = score_candidate(link, target_doc)
            # 5b/6. Keep track (and eventually go to) the page with the highest semantic similarity
            if similarity_score > semantic_similarity:
                print("Most similar changed to " + url_to_title(link))
                semantic_similarity = similarity_score
                current_url = link
        path.append(current_url)
        if current_url == target_url:
            return TraversalResult(
                success=True,
                path=path,
                steps_taken=step,
                start_url=start_url,
                target_url=target_url,
            )
        sleep(REQUEST_DELAY_SECONDS)

    return TraversalResult(
        success=False,
        path=path,
        steps_taken=step_limit,
        start_url=start_url,
        target_url=target_url,
        error="Step limit exceeded",
    )
