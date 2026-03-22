from wordfreq import zipf_frequency
from names_dataset import NameDataset
from urllib.parse import urljoin, urlparse
import spacy
import re


log_path=None
nd = NameDataset()
nlp = spacy.load("en_core_web_sm")


def is_valid_attorney_slug(word):
    if (nd.search(word)["first_name"] is not None and \
        "United States" in nd.search(word)["first_name"]["rank"] and \
        nd.search(word)["first_name"]["rank"]["United States"] is not None or \
        nd.search(word)["last_name"] is not None and \
        "United States" in nd.search(word)["last_name"]["rank"] and \
        nd.search(word)["last_name"]["rank"]["United States"] is not None) and \
        (nd.search(word)["first_name"] is not None and \
        'gender' in nd.search(word)["first_name"] and \
        nd.search(word)["first_name"]["gender"] or \
        nd.search(word)["last_name"] is not None and \
        'gender' in nd.search(word)["last_name"] and \
        nd.search(word)["last_name"]["gender"]) or \
        '.' in word or len(word) <= 2:
        return True

    word_clean = word.strip().lower()
    if any(keyword in word_clean for keyword in ["contact", "team", "help", "info", "support", "service", "mail", "admin", "office", "attorney", "lawyer", "legal"]):
        return False

    # Reject very common English words
    if zipf_frequency(word_clean, "en") > 3.5:
        return False

    return True


def path_depth(url):
    path = urlparse(url).path.strip("/")
    if not path:
        return 0
    return len(path.split("/"))

def is_profile_slug(url, is_profil_check=True):
    path = urlparse(url).path.strip("/")
    if not path:
        return False

    slug = path.split("/")[-1]
    if is_profil_check:
        slug = slug.replace("attorney", "", 1)
        slug = slug.replace("lawyer", "", 1)
        slug = slug.replace("profile", "", 1)
        slug = slug.replace("%2C", "", 1)
    words = slug.split("-")

    if not (2 <= len(words) <= 4):
        return False

    blacklist = {"team", "attorneys", "lawyers", "meet", "about"}
    if any(w.lower() in blacklist for w in words):
        return False

    for w in words:
        if not re.match(r'^[a-zA-Z]+$', w):
            return False

    return True

def looks_like_name(text):
    words = text.strip().split()
    if not (len(words) <= 4 and len(words) >= 2):
        return False
    if "www" in text.lower():
        return False
    if not all(w[0].isalpha() or '.' in w for w in words if w):
        return False
    for w in words:
        if not is_valid_attorney_slug(w.lower().replace(',', '')):
            return False
    return True

