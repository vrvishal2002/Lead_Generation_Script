from wordfreq import zipf_frequency
from names_dataset import NameDataset
from urllib.parse import urlparse
import re

log_path=None
nd = NameDataset()

# Legal title suffixes that appear in URLs/names but are not name words
_NAME_NOISE = frozenset([
    "inc", "incx", "profile",
    "esq", "jr", "sr", "ii", "iii", "iv",
    "jd", "phd", "md", "llm", "atty", "pc", "pa",
])


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


def is_valid_lead_slug(word, blacklist=None):
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
    if any(keyword in word_clean for keyword in ["contact", "help", "info", "support", "service", "mail", "admin", "office"]):
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

def is_profile_slug(url, is_profil_check=True, blacklist=None):
    path = urlparse(url).path.strip("/")
    if not path:
        return False

    if path[-1] != '/':
        slug = " ".join(path.split('/')[-1].split('-'))
    else:
        slug =" ".join(path.split('/')[-2].split('-'))

    if is_profil_check:
        slug = normalize_name(slug, blacklist)

    words = slug.split()

    if not (2 <= len(words) <= 4):
        # Allow single-word slugs that look like a combined first+last name
        # (e.g. /sfleming, /kpenrod) — rare English word signals a name
        if len(words) == 1 and len(words[0]) >= 5 and re.match(r'^[a-zA-Z]+$', words[0]):
            return is_strong_name_word(words[0])
        return False

    if blacklist and any(w.lower() in blacklist for w in words):
        return False

    for w in words:
        if not re.match(r'^[a-zA-Z]+$', w):
            return False

    return True


def looks_like_name(text, exclusions=None):
    text = normalize_name(text, exclusions)
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


def normalize_name(name, exclusions=None):
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
    # Industry-agnostic noise removal (word-level; full list in _NAME_NOISE)
    name = " ".join(w for w in name.split() if w.lower() not in _NAME_NOISE)

    if exclusions:
        for word in exclusions:
            name = name.replace(word.lower(), "", 1)

    name = name.replace("%2C", "", 1)

    name = re.sub(r"[-_.]", " ", name)

    # Strip legal title suffixes as whole words (esq, jr, sr, jd, etc.)
    name = " ".join(w for w in name.split() if w.lower() not in _NAME_NOISE)

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

    def _middles_ok(m_a, m_b):
        if m_a and m_b:
            return any(i in [x[0] for x in m_b] for i in [x[0] for x in m_a])
        return True

    # Standard order: First + Last match
    if f1 == f2 and l1 == l2:
        return _middles_ok(m1, m2)

    # Reversed slug format: URL stores "last-first[-middle]" (e.g. /hozubin-rebecca-j/)
    # split_name("hozubin rebecca j") → f2="hozubin", m2=["rebecca"], l2="j"
    # We need: n1's last (l1) == n2's first word (f2), and n1's first (f1) appears in n2 remainder
    if f1 and l1 and f2:
        if l1 == f2:
            # n2's remaining words (middle + last) must contain f1
            n2_rest = list(m2 or []) + ([l2] if l2 else [])
            if n2_rest and f1 in n2_rest:
                return True
        # Also try the other direction (n1 is the reversed slug, n2 is the display name)
        if l2 and l2 == f1:
            n1_rest = list(m1 or []) + ([l1] if l1 else [])
            if n1_rest and f2 in n1_rest:
                return True

    return False
