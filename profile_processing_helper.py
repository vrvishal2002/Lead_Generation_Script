import re
import copy
from urllib.parse import urljoin, urlparse, unquote
import name_processor_lib
import soup_content_lib
from log_lib import log

# Section header pattern — headings that introduce a list of attorneys/team members
# but are NOT names themselves. Used in the section-header scan pass.
_SECTION_HEADER_RE = re.compile(
    r'\b(attorneys|lawyers|team\s+members?|our\s+team|counsel|partners?|associates?|staff)\s*:?\s*$',
    re.IGNORECASE
)

# Patterns like "Law Office of X", "Law Offices of X" that prefix an attorney name in headings.
_FIRM_PREFIX_RE = re.compile(
    r'^(the\s+)?'
    r'(law\s+office[s]?\s+of|law\s+firm\s+of|offices\s+of|office\s+of|'
    r'law\s+group|attorneys?\s+at\s+law|law\s+offices?\s+of)\s*[,:\-]?\s*',
    re.IGNORECASE
)

# Single intro words before a name: "Meet John Smith", "About Sara Bloom"
_INTRO_WORD_RE = re.compile(
    r'^(meet|about|welcome|introducing|our|the|attorney|lawyer)\s+',
    re.IGNORECASE
)

# Trailing professional titles after a name: "John Smith, Attorney at Law"
_TITLE_SUFFIX_RE = re.compile(
    r',?\s*(attorney\s+at\s+law|trial\s+lawyer|attorney|lawyer|esq\.?|'
    r'counsel|partner|associate|founder|owner|paralegal|j\.?d\.?|p\.?c\.?)$',
    re.IGNORECASE
)

# Non-HTML file extensions to skip as directory/profile candidates
_SKIP_EXTENSIONS = frozenset([
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.mp4', '.mp3', '.avi', '.mov',
])


def _strip_firm_prefix(text: str) -> str:
    text = _FIRM_PREFIX_RE.sub("", text).strip()
    text = _INTRO_WORD_RE.sub("", text).strip()
    text = _TITLE_SUFFIX_RE.sub("", text).strip()
    return text


def _is_scrapable_url(url: str) -> bool:
    """Return False for non-HTML URLs: mailto:, tel:, image files, etc."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    path_lower = parsed.path.lower().rstrip("/")
    ext = "." + path_lower.rsplit(".", 1)[-1] if "." in path_lower.split("/")[-1] else ""
    if ext in _SKIP_EXTENSIONS:
        return False
    return True


class ProfileProcessingHelper:

    def __init__(self, log_path=None, config=None):
        self.log_path = log_path
        self.config = config or {}
        self.profile_keywords = self.config.get("profile_required_keywords", [])
        self.directory_keywords = self.config.get("directory_keywords", [])
        self.role_keywords = self.config.get("lead_role_keywords", [])
        self.slug_exclusions = self.config.get("slug_exclusion_keywords", [])
        
        name_processor_lib.log_path = log_path
        soup_content_lib.log_path = log_path


    def extract_profile_links(self, directory_url, home_url):
        soup = soup_content_lib.get_soup(directory_url)
        if not soup:
            log(f"not soup for directory: {directory_url}", self.log_path)
            return []

        is_home_dir = False
        if directory_url == home_url:
            is_home_dir = True

        if not is_home_dir:
            soup = soup_content_lib.clean_dom(soup)

        main = soup_content_lib.get_main_content(soup, is_home_dir)
        profile_links = []

        for link in main.find_all("a", href=True):
            full = urljoin(directory_url, link["href"])
            # Skip non-HTML URLs (mailto:, tel:, images, PDFs, etc.)
            if not _is_scrapable_url(full):
                continue
            name = ""
            if full[-1] != '/':
                name = " ".join(full.split('/')[-1].split('-'))
            else:
                name =" ".join(full.split('/')[-2].split('-'))
            name = name_processor_lib.normalize_name(name, self.slug_exclusions)

            home_netloc = urlparse(home_url).netloc
            full_netloc = urlparse(full).netloc
            # Guard against empty netloc (e.g. from relative URLs that became mailto-like)
            if not full_netloc:
                continue
            if (home_netloc in full_netloc or full_netloc in home_netloc) and \
                name_processor_lib.looks_like_name(name) and name_processor_lib.is_profile_slug(full, blacklist=self.directory_keywords):
                profile_links.append(full)

        return list(set(profile_links))

    
    
    def get_main_content_profile(self, soup):
        combined = soup.new_tag("div")
        combined["id"] = "combined-content"

        include_name = r"(content|main|article|hero)"
        include_element = ["main", "article"]
        exclude_element = ["header", "footer", "nav"]

        pattern = re.compile(include_name, re.I)

        for tag in soup.find_all(True):

            class_attr = " ".join(tag.get("class", []))
            id_attr = tag.get("id", "")

            if (
                pattern.search(class_attr)
                or pattern.search(id_attr)
                or tag.name in include_element
            ):
                combined.append(copy.copy(tag))

        # ✅ If we found content → return combined

        if not combined.find('h1') and soup.find('h1'):
            for tag in soup.find('h1'):
                combined.append(copy.copy(tag))
                if combined.find('h1'):
                    break

        if combined.contents:
            return combined

        for tag in soup.find_all(exclude_element):
            tag.decompose()
        return soup
    
    
    # =====================================================
    # HELPERS
    # =====================================================

    def _validate_heading_name(self, raw_text):
        """Return a valid name string from heading text, or '' if not a person name."""
        # Strip firm-name prefixes like "Law Office of"
        text = _strip_firm_prefix(raw_text).strip()
        if not text:
            return ""
        # Validate every word is a name word
        for w in text.split():
            if not name_processor_lib.is_strong_name_word(w):
                return ""
        return text

    def _extract_name_from_headings(self, main, page_text, name_in_link, url):
        """Try h1 (all), h2, h3, h4 in order to find the attorney name on a profile page."""
        for tag_name in ["h1", "h2", "h3", "h4"]:
            for heading in main.find_all(tag_name):
                # Gather text from heading and its inline children
                children_texts = [c.get_text(strip=True) for c in heading.find_all()]
                for child in heading.find_all():
                    child.decompose()
                raw = heading.get_text(strip=True)

                candidates = [raw] + children_texts
                for candidate in candidates:
                    if not candidate:
                        continue
                    name = self._validate_heading_name(candidate)
                    if not name:
                        continue
                    name_in_page = name_processor_lib.normalize_name(name, exclusions=self.slug_exclusions)
                    if not name_in_link or name_processor_lib.names_match(name_in_page, name_in_link):
                        return name
                    # Name doesn't match URL slug — keep going only if all words appear in page text
                    if all(w in page_text for w in name_in_link.split()):
                        log(f"Name '{name_in_page}' doesn't match url slug '{name_in_link}' for {url}, using url slug", self.log_path)
                        return name_in_link
                    log(f"Name '{name_in_page}' doesn't match url slug '{name_in_link}' for {url}, discarding", self.log_path)
        return ""

    # =====================================================
    # PROFILE EXTRACTION
    # =====================================================

    def extract_profile(self, url, firm_name="", firm_body_text=""):
        soup = soup_content_lib.get_soup(url)
        if not soup:
            return None

        soup = soup_content_lib.clean_dom(soup)
        body_text = soup.get_text(separator=" ", strip=True).lower()
        # Skip keyword check if the firm's home/directory page already matched
        if self.profile_keywords and not firm_body_text:
            if not any(keyword.lower() in body_text for keyword in self.profile_keywords):
                log(f"Profile does not match required keywords: {self.profile_keywords}", self.log_path)
                return None
        if self.role_keywords and not any(keyword.lower() in body_text for keyword in self.role_keywords):
            log(f"Profile does not match required roles: {self.role_keywords}", self.log_path)
            return None
        main = self.get_main_content_profile(soup)

        text = main.get_text(" ", strip=True)
        name_in_link = name_processor_lib.normalize_name(
            url.strip('/').split('/')[-1].split('.')[0], exclusions=self.slug_exclusions
        )

        name = self._extract_name_from_headings(main, text, name_in_link, url)

        if name == "":
            name_from_url = url.strip('/').split('/')[-1].replace('-', ' ').replace('_', ' ')
            if not name_processor_lib.looks_like_name(name_from_url):
                return None
            name = name_from_url.capitalize()

        phone_match = re.search(r'\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}', text)
        phone = phone_match.group() if phone_match else ""

        email_tag = main.find("a", href=re.compile("mailto:"))
        email = email_tag["href"].replace("mailto:", "") if email_tag else ""
        if email and not name_processor_lib.is_strong_name_word(email.split('@')[0]):
            email = ""

        normalized_name = name_processor_lib.normalize_name(name, self.slug_exclusions)

        return {
            "Name": normalized_name,
            "Phone": phone,
            "Email": email,
            "Profile URL": url
        }

    def get_profile_name_from_text(self, text, title_keywords):

        # Skip tiny or irrelevant blocks
        if len(text) < 10 or len(text) > 50:
            return None

        is_profile_tag = False
        for line in text.split("\n"):
            line_clean = line.strip().lower()
            title = None
            for k in title_keywords:
                if k in line_clean:
                    title = k
                    break
            if title:
                for k in title_keywords:
                    if k is not title:
                        line_clean = line_clean.replace(k, '')
                line_clean = line_clean.replace('/', ' ').replace('&', ' ')  # Debugging output
                if line_clean.split(title)[0].strip():
                    name = line_clean.split(title)[0].split('\n')[-1].strip()  # In case it's "John Doe, Attorney"
                else:
                    name = line_clean.split(title)[-1].split('\n')[-1].strip()  # In case it's "John Doe Attorney"
                if name_processor_lib.looks_like_name(name):
                    is_profile_tag = True
                    break
        if is_profile_tag:
            return name
        return None


    def extract_team_profiles(self, soup, url, firm_name=""):
        profiles = []

        # Common tags that hold cards
        candidate_tags = soup.find_all(["div", "section", "li", "h2", "h1", "p"])
        
        # Combine industry roles with generic organizational titles
        title_keywords = list(set(self.role_keywords + ["about", "senior", "principal", "associate", "founder", "owner"]))

        for tag in candidate_tags:
            text = tag.get_text(" ", strip=True)

            # Try to find title
            name = self.get_profile_name_from_text(text, title_keywords)

            if not name:
                children = tag.find_all(recursive=False)[:2]
                text = " ".join([child.get_text(strip=True) for child in children])
                name = self.get_profile_name_from_text(text, title_keywords)

            if not name:
                continue

            # Extract email if present
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
            email = email_match.group() if email_match else None

            phone_match = re.search(r'\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}', text)
            phone = phone_match.group() if phone_match else ""

            normalized_name = name_processor_lib.normalize_name(name, exclusions=self.slug_exclusions)

            profiles.append({
                "Name": normalized_name,
                "Phone": phone,
                "Email": email,
                "Profile URL": url
            })

        # ── Heading-scan pass: pick up h2/h3/h4 names with role in adjacent siblings ──
        # Handles patterns like:
        #   <h3>Carson Honeycutt</h3><strong>Founder, Attorney</strong>
        #   <h4>Meet Eric Derleth</h4>  (intro word stripped)
        #   <h2>Josh B. Cooley</h2><h4>Cases of Note</h4>
        seen_heading_names = {p["Name"] for p in profiles}
        extended_role_kw = set(title_keywords)
        for heading in soup.find_all(["h2", "h3", "h4"]):
            heading_text = heading.get_text(" ", strip=True)
            if len(heading_text) < 3 or len(heading_text) > 60:
                continue
            # Strip firm prefix before name check
            heading_text = _strip_firm_prefix(heading_text)
            if not name_processor_lib.looks_like_name(heading_text):
                continue

            # Build context from heading + next 3 siblings + parent container
            context_parts = [heading_text]
            sib = heading.find_next_sibling()
            for _ in range(3):
                if not sib:
                    break
                context_parts.append(sib.get_text(" ", strip=True))
                sib = sib.find_next_sibling()
            parent = heading.parent
            if parent:
                context_parts.append(parent.get_text(" ", strip=True))
            context = " ".join(context_parts).lower()

            if not any(k in context for k in extended_role_kw):
                continue

            normalized_name = name_processor_lib.normalize_name(heading_text, exclusions=self.slug_exclusions)
            if normalized_name in seen_heading_names:
                continue
            seen_heading_names.add(normalized_name)

            # Try to get email/phone from the card context
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', " ".join(context_parts))
            phone_match = re.search(r'\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}', " ".join(context_parts))

            profiles.append({
                "Name": normalized_name,
                "Phone": phone_match.group() if phone_match else "",
                "Email": email_match.group() if email_match else None,
                "Profile URL": url
            })

        # ── Section-header scan: extract names listed under "Attorneys:", "Our Team:" etc ──
        # Handles patterns like:
        #   <h3>Attorneys:</h3>  <p>Darryl Thompson<br>Tyler Wright, Esq.</p>
        # where the heading is a section label, not a name, and names follow as siblings.
        # Each sibling is split by newlines (from <br> tags) to handle multiple names per element.
        seen_names_so_far = {p["Name"] for p in profiles}
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            header_raw = heading.get_text(" ", strip=True)
            if not _SECTION_HEADER_RE.search(header_raw):
                continue
            # Walk following siblings until we hit another heading
            sib = heading.find_next_sibling()
            while sib:
                if sib.name in ("h1", "h2", "h3", "h4"):
                    break
                # Split on newlines (covers <br>-separated names in one element)
                for line in sib.get_text("\n", strip=True).split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    norm = name_processor_lib.normalize_name(line, self.slug_exclusions)
                    if name_processor_lib.looks_like_name(norm) and norm not in seen_names_so_far:
                        seen_names_so_far.add(norm)
                        email_m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', line)
                        phone_m = re.search(r'\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}', line)
                        profiles.append({
                            "Name": norm,
                            "Phone": phone_m.group() if phone_m else "",
                            "Email": email_m.group() if email_m else None,
                            "Profile URL": url
                        })
                sib = sib.find_next_sibling()

        # Remove duplicates
        unique_profiles = []
        seen = set()

        for p in profiles:
            key = p["Name"]
            if key not in seen:
                seen.add(key)
                unique_profiles.append(p)

        log(f"Extracted {len(unique_profiles)} profiles from directory page: {url}", self.log_path)
        for p in unique_profiles:
            log(p, self.log_path)

        return unique_profiles
