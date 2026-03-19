from urllib.parse import urljoin, urlparse
import name_processor_lib 
import soup_content_lib 
from log_lib import log



class DirectoryProcessingHelper:

    def __init__(self, log_path=None):
        self.log_path = log_path
        name_processor_lib.log_path = log_path
        soup_content_lib.log_path = log_path


    DIRECTORY_KEYWORDS = [
        "attorneys",
        "our-team",
        "team",
        "legal-team",
        "lawyers",
        "meet",
        "profiles"
    ]


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
            and urlparse(full).netloc not in domain.replace("www.", ''):
                continue

            lower = full.lower()
            if any(k in lower for k in self.DIRECTORY_KEYWORDS):
                candidates.append(full)

        log(list(set(candidates)), self.log_path)
        return list(set(candidates))



    def score_directory_candidate(self, directory_url, home_url):
        score = 0

        depth = name_processor_lib.path_depth(directory_url)

        if depth <= 1:
            score += 5
        elif depth == 2:
            score += 2
        else:
            score -= 5

        if name_processor_lib.is_profile_slug(directory_url, False):
            score -= 10

        soup = soup_content_lib.get_soup(directory_url)
        if not soup:
            return -999

        soup = soup_content_lib.clean_dom(soup)
        main = soup_content_lib.get_main_content(soup, depth == 0)

        # Header check
        h1 = main.find("h1")
        if h1:
            header_text = h1.get_text(strip=True).lower()
            if any(k in header_text for k in ["our team", "attorneys", "meet", "lawyers"]):
                score += 5

        # Count distinct names in main content only
        name_links = set()

        for link in main.find_all("a", href=True):
            full = urljoin(directory_url, link["href"])
            name = ""
            if full[-1] != '/':
                name = " ".join(full.split('/')[-1].split('-'))
            else:
                name =" ".join(full.split('/')[-2].split('-'))
                name = name.replace("attorney", "", 1)
                name = name.replace("lawyer", "", 1)
                name = name.replace("profile", "", 1)

            if (urlparse(home_url).netloc in urlparse(full).netloc or \
                urlparse(full).netloc in urlparse(home_url).netloc) and \
                name_processor_lib.looks_like_name(name):
                name_links.add(name)

        if len(name_links) >= 4:
            score += 5
        else:
            score -= 3

        # Penalize heavy paragraph density (likely practice page)
        if len(' '.join([p.get_text() for p in main.find_all("p")]).split()) > 200:
            score -= 3

        return score

    def select_best_directory(self, candidates, home_url):
        domain = urlparse(home_url).netloc
        valid_candidates = [
            u for u in candidates
            if urlparse(u).netloc in domain or domain in urlparse(u).netloc
        ]

        scored = [(url, self.score_directory_candidate(url, home_url)) for url in valid_candidates]

        scored.sort(key=lambda x: x[1], reverse=True)

        log("\nDirectory Scoring:", self.log_path)
        for u, s in scored:
            log(f"{s:3} → {u}", self.log_path)

        if not scored:
            return None

        best_url, best_score = scored[0]

        if best_score <= 0:
            return None

        if best_score < 7:
            return home_url

        dir_urls_s = []
        for u, s in scored:
            count = 0
            if s < best_score:
                break
            keys = ['our', 'team', 'attorneys', 'meet', 'people', 'lawyer']
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