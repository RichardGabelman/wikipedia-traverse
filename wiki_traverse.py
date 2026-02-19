import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests
import spacy
from bs4 import BeautifulSoup

WIKIPEDIA_BASE_URL = "https://en.wikipedia.org"
WIKIPEDIA_ARTICLE_PREFIX = "/wiki/"

REQUEST_DELAY_SECONDS = 2
DEFAULT_STEP_LIMIT = 10
DEFAULT_BEAM_WIDTH = 5

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
    steps_taken: int
    elapsed_seconds: float
    path: list[str]
    start_url: str
    target_url: str
    error: Optional[str] = None

    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILURE"
        path_str = " -> ".join(url_to_title(url) for url in self.path)
        return (
            f"[{status}] {self.steps_taken} steps in {self.elapsed_seconds:.1f}s\n"
            f"Path: {path_str}"
        )


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


def score_candidates(
    candidate_urls: list[str], target_doc, beam_width: int
) -> list[tuple[float, str]]:
    """
    Score each candidate URL by semantic similarity of its title to the
    target title. Returns the top `beam_width` candidates as
    (score, url) tuples, sorted descending by score.
    """
    scored: list[tuple[float, str]] = []
    for url in candidate_urls:
        title = url_to_title(url)
        similarity = target_doc.similarity(nlp(title))
        scored.append((similarity, url))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:beam_width]


def reconstruct_path(
    parents: dict[str, str], start_url: str, target_url: str
) -> list[str]:
    """Return the path taken via a backwards trace through parents."""
    path = []
    current = target_url
    while current != start_url:
        path.append(current)
        current = parents[current]
    path.append(start_url)
    path.reverse()
    return path


def traverse_wiki(
    start_url: str,
    target_url: str,
    step_limit: int = DEFAULT_STEP_LIMIT,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    verbose: bool = False,
) -> TraversalResult:
    """
    Attempt to navigate from `start_url` to `target_url` using Wikipedia links.

    Strategy - beam search guided by NLP semantic similarity:
        At each step, we maintain a frontier of up to `beam_width` candidate
        pages. We extract the Wikipedia article links from each page,
        the article title from each url, and perform semantic analysis
        between it and the target title. The `beam_width` articles whose
        titles are the most semantically similar are moved to the frontier
        and the process repeats.

    Args:
        start_url: Full Wikipedia article URL to start from.
        target_url: Full Wikipedia article URL to reach.
        step_limit: Maximum number of page hops before giving up.
        beam_width: The number of candidate pages we expand at each step.
        verbose: Whether the logger will show debug statements.

    Returns:
        A TraversalResult with success flag, start/target urls, and error info.
    """
    start_time = time.monotonic()

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle start_url == target_url edge case.
    if start_url == target_url:
        elapsed = time.monotonic() - start_time
        return TraversalResult(
            success=True,
            steps_taken=0,
            elapsed_seconds=elapsed,
            path=[start_url],
            start_url=start_url,
            target_url=target_url,
        )

    target_title = url_to_title(target_url)
    target_doc = nlp(target_title)
    logger.info("Target: '%s'", target_title)
    logger.info("Start:  '%s'", url_to_title(start_url))
    logger.info("Step limit: %d", step_limit)

    # visited tracks every URL we have ever processed, NLP-wise
    # so we never process the same page twice
    visited: set[str] = {start_url}

    # frontier is the set of pages we will process next.
    frontier: list[str] = [start_url]

    # parents track which visited article preceded another
    # child : parent
    parents: dict[str, str] = {}

    session = requests.Session()
    session.headers.update({"User-Agent": "WikiTraversal/2.0 (education project)"})

    for step in range(step_limit):
        if not frontier:
            logging.warning("Frontier is empty, no pages to explore.")
            break

        candidate_urls: list[str] = []
        for current_url in frontier:
            soup = fetch_page(current_url, session)
            if soup is None:
                continue

            page_links = extract_article_links(soup)
            non_visited_links = [link for link in page_links if link not in visited]

            for link in non_visited_links:
                parents[link] = current_url

            candidate_urls.extend(non_visited_links)

            # Respect Wikipedia's rate limits.
            time.sleep(REQUEST_DELAY_SECONDS)

        top_frontier_scorers = score_candidates(candidate_urls, target_doc, beam_width)

        if not top_frontier_scorers:
            logger.warning("No scorable candidates found.")
            break

        for rank, (score, url) in enumerate(top_frontier_scorers, 1):
            logger.info("Candidate #%d (%.4f) %s", rank, score, url_to_title(url))

        best_score, best_url = top_frontier_scorers[0]
        logger.info("Best this step: `%s` (%.4f)", url_to_title(best_url), best_score)

        # Check if we found the target.
        if best_url == target_url:
            elapsed = time.monotonic() - start_time
            path = reconstruct_path(parents, start_url, target_url)
            logger.info("Target reached at step %d!", step + 1)
            return TraversalResult(
                success=True,
                steps_taken=step + 1,
                elapsed_seconds=elapsed,
                path=path,
                start_url=start_url,
                target_url=target_url,
            )

        frontier = [url for score, url in top_frontier_scorers]
        visited.update(frontier)

    # Step limit exceeded.
    elapsed = time.monotonic() - start_time
    logger.warning("Traversal ended without reaching target.")
    return TraversalResult(
        success=False,
        steps_taken=step_limit,
        elapsed_seconds=elapsed,
        path=[],
        start_url=start_url,
        target_url=target_url,
        error="Step limit exceeded",
    )


if __name__ == "__main__":
    start_url = "https://en.wikipedia.org/wiki/Philosophy"
    target_url = "https://en.wikipedia.org/wiki/Pizza"
    result = traverse_wiki(start_url, target_url, 15, True)
    print(result)
