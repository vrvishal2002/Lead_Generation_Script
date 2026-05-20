from urllib.parse import urljoin, urlparse
import name_processor_lib 
import soup_content_lib 
from log_lib import log
from profile_processing_helper import ProfileProcessingHelper
from bs4 import BeautifulSoup



class DirectoryProcessingHelper:

    def __init__(self, log_path=None, config=None):
        self.log_path = log_path
        self.config = config or {}
        self.directory_keywords = self.config.get("directory_keywords", ["team", "about", "staff"])
        name_processor_lib.log_path = log_path
        soup_content_lib.log_path = log_path


    def collect_directory_candidates(self, home_url):
        soup = soup_content_lib.get_soup(home_url)
        if not soup:
            return []
        candidates = []
        domain = urlparse(home_url).netloc

        for link in soup.find_all("a", href=True):
            href = link["href"]
            full = urljoin(home_url, href)
            if domain.replace("www.", '') not in urlparse(full).netloc \
            and urlparse(full).netloc not in domain.replace("www.", '') or not urlparse(full).netloc:
                continue

            lower = full.lower()
            if any(k in lower for k in self.directory_keywords):
                candidates.append(full)

        log(f"{home_url} - Directory Candiates: {list(set(candidates))}", self.log_path)
        return list(set(candidates))


    def score_directory_candidate(self, directory_url, home_url):
        score = 0

        depth = name_processor_lib.path_depth(directory_url)

        if depth <= 1:
            score += 4
        elif depth == 2:
            score += 3
        else:
            score -= 4

        if name_processor_lib.is_profile_slug(directory_url, is_profil_check=False, blacklist=self.directory_keywords):
            score -= 10

        soup = soup_content_lib.get_soup(directory_url)
        if not soup:
            return -999
        soup_copy = BeautifulSoup(str(soup), "html.parser")

        cleaned_soup = soup_content_lib.clean_dom(soup_copy)
        main = soup_content_lib.get_main_content(cleaned_soup, depth == 0)

        h1 = main.find_all("h1")
        if not h1:
            # In Header element checcking for h1
            h1 = []
            header_tags = soup.find_all("header")
            if header_tags:
                for header_tag in header_tags:
                    h1_tags = header_tag.find_all("h1")
                    if h1_tags:
                        for el in h1_tags:
                            h1.append(el)

        if h1:
            header_text = " ".join([el.get_text(strip=True).lower() for el in h1])
            if any(k in header_text for k in self.directory_keywords):
                score += 4
            else:
                # Checking for all h2 in main content
                h2 = main.find_all("h2")
                if h2:
                    header_text = " ".join([el.get_text(strip=True).lower() for el in h2])
                    if any(k in header_text for k in self.directory_keywords):
                        score += 4

        # Count distinct names in main content only
        name_links = set()

        for link in main.find_all("a", href=True):
            full = urljoin(directory_url, link["href"])
            name = ""
            if full[-1] != '/':
                name = " ".join(full.split('/')[-1].split('-'))
            else:
                name =" ".join(full.split('/')[-2].split('-'))
            name = name_processor_lib.normalize_name(name, self.config.get("slug_exclusion_keywords", []))

            if urlparse(full).netloc and (urlparse(home_url).netloc in urlparse(full).netloc or \
                urlparse(full).netloc in urlparse(home_url).netloc) and \
                name_processor_lib.looks_like_name(name):
                name_links.add(name)

        profiles_count = len(name_links)
        if len(name_links):
            if len(name_links) >= 3:
                score += 6
            else:
                score += len(name_links) * 2  # small boost for 1-2 names
        else:
            profiles = ProfileProcessingHelper(self.log_path, config=self.config).extract_team_profiles(main, directory_url)
            profiles_count = len(profiles)
            if profiles_count:
                if profiles_count >= 3:
                    score += 6
                else:
                    score += profiles_count * 1.5 # small boost for 1-2 profiles
            else:
                score -= 3

        # Penalize heavy paragraph density (likely practice page)
        if len(' '.join([p.get_text() for p in main.find_all("p")]).split()) > 200:
            score -= 2

        return score, profiles_count


    def select_best_directory(self, candidates, home_url):
        domain = urlparse(home_url).netloc
        valid_candidates = [
            u for u in candidates
            if urlparse(u).netloc in domain or domain in urlparse(u).netloc
        ]

        scored = [(url, self.score_directory_candidate(url, home_url)) for url in valid_candidates]

        scored.sort(key=lambda x: (x[1][0], x[1][1]), reverse=True)

        log("\nDirectory Scoring:", self.log_path)
        for u, s in scored:
            log(f"{s[0]}, {s[1]} → {u}", self.log_path)

        if not scored:
            return None

        best_url, best_score = scored[0]

        if best_score[0] <= 0:
            return None

        if best_score[0] < 7 or not best_score[1]:
            return home_url

        dir_urls_s = []
        for u, s in scored:
            count = 0
            if s[0] < best_score[0]:
                break
            if not s[1]:
                continue
            
            keys = self.directory_keywords
            b_u = u
            if b_u[-1] == '/':
                b_u = u[:-1]
            u_w_list = b_u.split('/')[-1].split('-')
            for w in u_w_list:
                if w in keys:
                    count += 1
            dir_urls_s.append((u, count))

        dir_urls_s.sort(key=lambda x: x[1], reverse=True)

        return dir_urls_s[0][0]