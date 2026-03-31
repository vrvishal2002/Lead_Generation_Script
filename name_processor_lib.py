from wordfreq import zipf_frequency
from names_dataset import NameDataset
from urllib.parse import urljoin, urlparse
import name_processor_lib
import spacy
import re


log_path=None
nd = NameDataset()
nlp = spacy.load("en_core_web_sm")
DIRECTORY_KEYWORDS = [
    "attorneys",
    "our-team",
    "team",
    "legal-team",
    "lawyers",
    "meet",
    "profiles",
    "about",
    "people",
    "paralegals",
    "advocates"
]


def is_strong_name_word(word):
    word = word.lower().strip(".,")

    # Initial like "a"
    if len(word) == 1:
        return True

    occ = nd.search(word)
    if not occ:
        return False

    first_name = occ.get("first_name")
    last_name = occ.get("last_name")

    # Safely extract ranks
    first_rank = None
    last_rank = None

    if first_name and isinstance(first_name, dict):
        first_rank = first_name.get("rank", {}).get("United States")

    if last_name and isinstance(last_name, dict):
        last_rank = last_name.get("rank", {}).get("United States")

    # 🔥 IMPORTANT: enforce rank threshold
    # Lower rank = more common name

    if first_rank and first_rank < 2000:
        return True

    if last_rank and last_rank < 2000:
        return True

    if zipf_frequency(word, "en") < 3:
        return True

    return False


def is_valid_attorney_slug(word):
    occurance = nd.search(word)
    if (occurance["first_name"] is not None and \
        "United States" in occurance["first_name"]["rank"] and \
        occurance["first_name"]["rank"]["United States"] is not None or \
        occurance["last_name"] is not None and \
        "United States" in occurance["last_name"]["rank"] and \
        occurance["last_name"]["rank"]["United States"] is not None) and \
        (occurance["first_name"] is not None and \
        'gender' in occurance["first_name"] and \
        occurance["first_name"]["gender"] or \
        occurance["last_name"] is not None and \
        'gender' in occurance["last_name"] and \
        occurance["last_name"]["gender"]) or \
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

    if path[-1] != '/':
        slug = " ".join(path.split('/')[-1].split('-'))
    else:
        slug =" ".join(path.split('/')[-2].split('-'))

    if is_profil_check:
        slug = name_processor_lib.normalize_name(slug)

    words = slug.split()

    if not (2 <= len(words) <= 4):
        return False

    blacklist = DIRECTORY_KEYWORDS
    if any(w.lower() in blacklist for w in words):
        return False

    for w in words:
        if not re.match(r'^[a-zA-Z]+$', w):
            return False

    return True


def looks_like_name(text):
    text = normalize_name(text)
    if not text:
        return False
    words = text.lower().replace(",", "").split()

    # Must be 2–4 words (names only)
    if len(words) < 2 or len(words) > 4:
        if len(words) == 1:
            if len(words[0]) < 4:
                return False
            return is_strong_name_word(words[0])
        else:
            return False


    strong_count = sum(is_strong_name_word(w) for w in words)

    # ✅ At least 2 strong name words required
    if strong_count < 2:
        return False

    # ✅ Majority must be strong
    if strong_count / len(words) < 1:
        return False

    return True


def normalize_name(name):
    # Lowercase
    name = name.lower().replace("/", " ").replace("\\", " ").replace('-', ' ').replace('_', ' ').strip()

    # Replace separators with space
    if name.endswith(".html"):
        name = name.replace(".html", "")
    if name.endswith(".shtml"):
        name = name.replace(".shtml", "")
    if name.endswith(".htm"):
        name = name.replace(".htm", "")
    if name.endswith(".php"):
        name = name.replace(".php", "")
    if name.endswith(".asp"):
        name = name.replace(".asp", "")
    if name.endswith(".aspx"):
        name = name.replace(".aspx", "")
    if name.endswith(".jsp"):
        name = name.replace(".jsp", "")
    if name.endswith(".cfm"):
        name = name.replace(".cfm", "")
    if name.endswith(".cgi"):
        name = name.replace(".cgi", "")
    if name.endswith(".pl"):
        name = name.replace(".pl", "")
    if name.endswith(".inc"):
        name = name.replace(".inc", "")
    if name.endswith(".incx"):
        name = name.replace(".incx", "")
    name = name.replace("attorney", "", 1)
    name = name.replace("lawyer", "", 1)
    name = name.replace("profile", "", 1)
    name = name.replace("%2C", "", 1)

    name = re.sub(r"[-_.]", " ", name)

    # Remove non-alphabet characters
    name = re.sub(r"[^a-z\s]", "", name)

    # Normalize spaces
    name = re.sub(r"\s+", " ", name).strip()

    return name


def split_name(name):
    parts = name.split()

    if len(parts) == 1:
        return parts[0], None, None

    if len(parts) == 2:
        return parts[0], None, parts[1]

    # first, middle(s), last
    return parts[0], parts[1:-1], parts[-1]


def names_match(name1, name2):
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)

    f1, m1, l1 = split_name(n1)
    f2, m2, l2 = split_name(n2)

    # First + Last must match
    if f1 != f2 or l1 != l2:
        return False

    # If both have middle names → check initial match
    if m1 and m2:
        m1_initials = [x[0] for x in m1]
        m2_initials = [x[0] for x in m2]

        return any(i in m2_initials for i in m1_initials)

    # If one has middle and other doesn't → still OK
    return True
