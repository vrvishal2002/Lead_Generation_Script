from directory_processing_helper import DirectoryProcessingHelper
from profile_processing_helper import ProfileProcessingHelper
import soup_content_lib
from log_lib import log



  
class FirmScraper: 

    def __init__(self, log_path=None, config=None):
        self.log_path = log_path
        self.config = config


    def scrape_firm(self, home_url, firm_name=""):
        log(f"Collecting directory candidates... for {home_url}", self.log_path)
        candidates = DirectoryProcessingHelper(log_path=self.log_path, config=self.config).collect_directory_candidates(home_url)

        log(f"\nPossible directory URLs for {home_url} :", self.log_path)
        for c in candidates:
            log(c, self.log_path)

        directory_url = DirectoryProcessingHelper(log_path=self.log_path, config=self.config).select_best_directory(candidates, home_url)

        if not directory_url:
            log(f"\nNo valid directory found for {home_url}. So going with home URL", self.log_path)
            directory_url = home_url

        log(f"\n{home_url} : Selected Directory:{directory_url}", self.log_path)

        # Check required keywords against the firm home page once — if it matches, skip
        # per-profile keyword checks (individual bios rarely repeat practice-area language).
        firm_body_text = ""
        profile_keywords = (self.config or {}).get("profile_required_keywords", [])
        if profile_keywords:
            home_soup = soup_content_lib.get_soup(home_url)
            if home_soup:
                firm_body_text = home_soup.get_text(separator=" ", strip=True).lower()
            if not any(kw.lower() in firm_body_text for kw in profile_keywords):
                # Home page didn't match; also try the directory page
                if directory_url != home_url:
                    dir_soup = soup_content_lib.get_soup(directory_url)
                    if dir_soup:
                        firm_body_text = dir_soup.get_text(separator=" ", strip=True).lower()
            if any(kw.lower() in firm_body_text for kw in profile_keywords):
                log(f"Firm {firm_name} home/directory page matches practice keywords — skipping per-profile keyword filter", self.log_path)
            else:
                log(f"Firm {firm_name} home/directory page does not match practice keywords — will filter per profile", self.log_path)
                firm_body_text = ""  # empty → per-profile check remains active

        profile_proc = ProfileProcessingHelper(log_path=self.log_path, config=self.config)

        profile_links = profile_proc.extract_profile_links(directory_url, home_url)
        log(f"\n{directory_url} : Profiles Found: {len(profile_links)}", self.log_path)

        if not profile_links:
            profile_links = profile_proc.extract_profile_links(home_url, home_url)
            log(f"\n{home_url} : Profiles Found in Directory: {len(profile_links)}", self.log_path)

        if not profile_links:
            soup = soup_content_lib.get_soup(directory_url)
            if soup:
                profiles = profile_proc.extract_team_profiles(soup, directory_url, firm_name=firm_name)
                if profiles:
                    log(f'\n{directory_url} : Profiles Found in Team: {len(profiles)}: {profiles}', self.log_path)
                return profiles

        if not profile_links:
            return []

        for p in profile_links:
            log(p, self.log_path)

        results = []
        for link in profile_links:
            log(f"Extracting: {link}", self.log_path)
            data = profile_proc.extract_profile(link, firm_name=firm_name, firm_body_text=firm_body_text)
            if data:
                results.append(data)

        return results


#     df = pd.DataFrame(profiles)
#     df.to_csv("attorney_profiles_final.csv", index=False)

#     log("\nSaved → attorney_profiles_final.csv")
#     # https://www.jacobs-jacobs.com/
#     # https://csgtrials.com/
#     # https://www.perecman.com/
