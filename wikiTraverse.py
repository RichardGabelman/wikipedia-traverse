import requests
from bs4 import BeautifulSoup
import spacy
from time import sleep

# startURL: https://en.wikipedia.org/wiki/XXXXX
# targetURL: https://en.wikipedia.org/wiki/YYYYY
# limit: optional int var that limits how many
# pages the program will search
# Returns 1 if a path is found, 0 if not

WIKIPEDIA_BASE_URL = "https://en.wikipedia.org"
WIKIPEDIA_ARTICLE_PREFIX = "/wiki/"

nlp = spacy.load("en_core_web_lg")

def traverseWiki(startURL, targetURL, limit=10):
  # Parse the targetURL for the semantic meaning of the title
  targetTitle = targetURL.rsplit('/', 1)[-1].replace("_", " ")
  targetSemantic = nlp(targetTitle)
  currentURL = startURL
  # Path keeps track of the pages we ultimately visit
  path = [currentURL]

  for i in range(limit):
    # 1. Determine if current URL is the target URL
    if currentURL == targetURL:
      print("Success!")
      print(path)
      return 1
    
    # 2. Go to valid URL
    response = requests.get(
      url=currentURL,
    )
    soup = BeautifulSoup(response.content, 'html.parser')

    # 3a. Collect links
    allLinks = soup.find(id="bodyContent").find_all("a")
    semanticSimilarity = 0

    for link in allLinks:
      # 3b. Sort out the 'bad' links
      # (non-Wiki references, non-main articles)
      linkHref = link.get('href', "")
      if linkHref.find(WIKIPEDIA_ARTICLE_PREFIX) == -1:
        continue
      if ((linkHref.find("/Special:") != -1) or 
          (linkHref.find("/Talk:") != -1) or 
          (linkHref.find("/Category:") != -1) or 
          (linkHref.find("/File:") != -1) or
          (linkHref.find("/Wikipedia:") != -1) or
          (linkHref.find("/Template:") != -1) or
          (linkHref.find("wikidata") != -1) or
          (linkHref.find("/Help:") != -1)):
        continue
      linkURL = WIKIPEDIA_BASE_URL + linkHref
      # 3c. In order to prevent loops, I prevent the
      # program from looking at links to pages
      # we've already traversed
      if linkURL in path:
        continue
      # 4. Parsing links for article titles
      currentTitle = linkURL.rsplit('/', 1)[-1].replace("_", " ")
      # 5a. Run a semantic comparison on each article title with the target article title
      currentSemantic = nlp(currentTitle)
      similarity = targetSemantic.similarity(currentSemantic)
      # 5b/6. Keep track (and eventually go to) the page with the highest semantic similarity
      if similarity > semanticSimilarity:
        print("Most similar changed to " + currentTitle)
        semanticSimilarity = similarity
        currentURL = linkURL
    path.append(currentURL)
    sleep(1)


  print("Failure! Traversal limit exceeded!")
  print(path)
  return 0

# Example of use:
# traverseWiki("https://en.wikipedia.org/wiki/Big_Bang", "https://en.wikipedia.org/wiki/Taylor_Swift", 10)
