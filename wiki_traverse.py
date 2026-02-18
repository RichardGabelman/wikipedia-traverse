import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class TraversalResult:
    """Structured result returned by traverse_wiki()."""

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
    """Extract an article title from a Wikipedia URL."""
    return url.rsplit("/", 1)[-1].replace("_", " ")


def is_valid_wiki_link(href: str) -> bool:
    """
    Return True for only links that point to main Wikipedia articles.
    Filters out anchors, external links, and non-article internal links.
    """
    if not href.startswith(WIKIPEDIA_ARTICLE_PREFIX):
        return False
    # Reject links containing a colon.
    # Colons indicate a non-main article namespace prefix.
    # (Talk:, Category:, etc.)
    # Plain article links never contain a colon.
    return ":" not in href.split("/wiki/", 1)[-1]


def extract_article_url(href: str) -> str:
    """Builds a complete Wikipedia URL from a relative href."""
    # Strip any in-page anchor (#Section)
    clean_href = href.split("#")[0]
    return WIKIPEDIA_BASE_URL + clean_href


def fetch_page(url: str, session: requests.Session) -> Optional[BeautifulSoup]:
    """
    Fetch a Wikipedia page and return a BeautifulSoup object.
    Returns None on error.
    """
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")
    except requests.RequestException as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def extract_article_links(soup: BeautifulSoup) -> list[str]:
    """
    Returns all unique, valid Wikipedia article URLs found in the
    main body content of a parsed page.
    """
    body = soup.find(id="bodyContent")
    if body is None:
        return []

    seen: set[str] = set()
    links: list[str] = []
    # Iterate through all links (a) with an href.
    # Links containing an href link to another article.
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
    """
    Score a candidate URL by semantic similarity of its title to the
    target title. Returns the semantic similarity value.
    """
    title = url_to_title(candidate_url)
    similarity = target_doc.similarity(nlp(title))

    return similarity


def traverse_wiki(
    start_url: str,
    target_url: str,
    step_limit: int = DEFAULT_STEP_LIMIT,
    verbose: bool = False,
) -> TraversalResult:
    """
    Attempt to navigate from `start_url` to `target_url` using Wikipedia links.

    Strategy - greedy search by NLP semantic similarity:
        At each page, we gather all unique links to other Wikipedia articles.
        We extract the title from each link, and perform semantic similarity
        analysis between it and the target title. The article whose title is
        the most semantically similar is moved to and the process repeats.

    Args:
        start_url: Full Wikipedia article URL to start from.
        target_url: Full Wikipedia article URL to reach.
        step_limit: Maximum number of page hops before giving up.
        verbose: Whether the logger will show debug statements.

    Returns:
        A TraversalResult with success flag, path, and error info.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle start_url == target_url edge case.
    if start_url == target_url:
        return TraversalResult(
            success=True,
            path=[],
            steps_taken=0,
            start_url=start_url,
            target_url=target_url,
        )

    target_title = url_to_title(target_url)
    target_doc = nlp(target_title)
    logger.info("Target: '%s'", target_title)
    logger.info("Start:  '%s'", url_to_title(start_url))
    logger.info("Step limit: %d", step_limit)

    current_url = start_url

    # Path keeps track of the pages we ultimately visit.
    path: list[str] = [current_url]

    session = requests.Session()
    session.headers.update({"User-Agent": "WikiTraversal/2.0 (education project)"})

    for step in range(step_limit):
        soup = fetch_page(current_url, session)
        if soup is None:
            continue

        links: list[str] = extract_article_links(soup)

        semantic_similarity = 0

        for link in links:
            # Prevent consideration of articles already visited.
            if link in path:
                continue

            similarity_score: int = score_candidate(link, target_doc)

            # Keep track of the page with the highest semantic similarity.
            if similarity_score > semantic_similarity:
                logger.debug("Most similar changed to %s (%.4f)", url_to_title(link), similarity_score)
                semantic_similarity = similarity_score
                current_url = link

        path.append(current_url)
        logger.info("Best this step: '%s' (%.4f)", url_to_title(current_url), semantic_similarity)

        # Check if we just selected the target.
        if current_url == target_url:
            logger.info("Target reached at step %d!", step + 1)
            return TraversalResult(
                success=True,
                path=path,
                steps_taken=step + 1,
                start_url=start_url,
                target_url=target_url,
            )

        # Respect Wikipedia's rate limits.
        sleep(REQUEST_DELAY_SECONDS)

    # Step limit exceeded.
    logger.warning("Traversal ended without reaching target.")
    return TraversalResult(
        success=False,
        path=path,
        steps_taken=step_limit,
        start_url=start_url,
        target_url=target_url,
        error="Step limit exceeded",
    )


if __name__ == "__main__":
    start_url = "https://en.wikipedia.org/wiki/Philosophy"
    target_url = "https://en.wikipedia.org/wiki/Pizza"
    result = traverse_wiki(start_url, target_url, 15, True)
    print(result)
