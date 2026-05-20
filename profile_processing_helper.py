import re
import copy
from urllib.parse import urljoin, urlparse, unquote
import name_processor_lib
import soup_content_lib 
from log_lib import log


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
            name = ""
            if full[-1] != '/':
                name = " ".join(full.split('/')[-1].split('-'))
            else:
                name =" ".join(full.split('/')[-2].split('-'))
            name = name_processor_lib.normalize_name(name, self.slug_exclusions)

            if (urlparse(home_url).netloc in urlparse(full).netloc or \
                urlparse(full).netloc in urlparse(home_url).netloc) and \
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
    # PROFILE EXTRACTION
    # =====================================================

    def extract_profile(self, url, firm_name=""):
        soup = soup_content_lib.get_soup(url)
        if not soup:
            return None

        soup = soup_content_lib.clean_dom(soup)
        body_text = soup.get_text(separator=" ", strip=True).lower()
        if self.profile_keywords and not any(keyword.lower() in body_text for keyword in self.profile_keywords):
            log(f"Profile does not match required keywords: {self.profile_keywords}", self.log_path)
            return None
        if self.role_keywords and not any(keyword.lower() in body_text for keyword in self.role_keywords):
            log(f"Profile does not match required roles: {self.role_keywords}", self.log_path)
            return None
        main = self.get_main_content_profile(soup)

        text = main.get_text(" ", strip=True)

        h1 = main.find("h1")
        name = ""
        if h1:
            # Remove all child tags (like span)
            children_h1 = []
            for child in h1.find_all():
                children_h1.append(child.get_text(strip=True) if child else "")
                child.decompose()
            name = h1.get_text(strip=True) if h1 else ""
            for w in name.split():
                if not name_processor_lib.is_strong_name_word(w):
                    name = ""
                    break
            if name == "":
                for words_name in children_h1:
                    name = words_name
                    for w in words_name.split():
                        if not name_processor_lib.is_strong_name_word(words_name):
                            name = ""
                            break
            if name:
                name_in_page = name_processor_lib.normalize_name(name, exclusions=self.slug_exclusions)
                name_in_link = name_processor_lib.normalize_name(url.strip('/').split('/')[-1].split('.')[0], exclusions=self.slug_exclusions)
                if not name_processor_lib.names_match(name_in_page, name_in_link):
                    if all(w in text for w in name_in_link.split()):
                        log(f"Name in h1: {name_in_page} doesn't match name in url: {name_in_link} for {url}, but all words are in page text. Using name from url.", self.log_path)
                        name = name_in_link
                    else:
                        log(f"Name in h1: {name_in_page} doesn't match name in url: {name_in_link} for {url}", self.log_path)
                        name = ""
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
