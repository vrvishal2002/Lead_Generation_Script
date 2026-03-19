from directory_processing_helper import DirectoryProcessingHelper
from profile_processing_helper import ProfileProcessingHelper
from log_lib import log




class FirmScraper:

    def __init__(self, log_path=None):
        self.log_path = log_path


    def scrape_firm(self, home_url):
        log("Collecting directory candidates...", self.log_path)
        candidates = DirectoryProcessingHelper(log_path=self.log_path).collect_directory_candidates(home_url)

        log("\nPossible directory URLs:", self.log_path)
        for c in candidates:
            log(c, self.log_path)

        directory_url = DirectoryProcessingHelper(log_path=self.log_path).select_best_directory(candidates, home_url)

        if not directory_url:
            log("\nNo valid directory found. So going with home URL", self.log_path)
            directory_url = home_url

        log(f"\nSelected Directory:{directory_url}", self.log_path)

        profile_links = ProfileProcessingHelper(log_path=self.log_path).extract_profile_links(directory_url, home_url)
        log(f"\nProfiles Found: {len(profile_links)}", self.log_path)

        if not profile_links:
            profile_links = ProfileProcessingHelper(log_path=self.log_path).extract_profile_links(home_url, home_url)
            log(f"\nProfiles Found in Directory: {len(profile_links)}", self.log_path)

        for p in profile_links:
            log(p, self.log_path)

        results = []
        for link in profile_links:
            log(f"Extracting: {link}", self.log_path)
            data = ProfileProcessingHelper(log_path=self.log_path).extract_profile(link)
            if data:
                results.append(data)

        return results


if __name__ == "__main__":
    profiles = FirmScraper().scrape_firm("https://www.alanbarry.com/")
    log(profiles)
    log("fds\ndf")
    firm_url = "https://www.doctorlawyerteam.com/"

    profiles = FirmScraper().scrape_firm("https://www.brandonjbroderick.com/")
    log(profiles)

#     df = pd.DataFrame(profiles)
#     df.to_csv("attorney_profiles_final.csv", index=False)

#     log("\nSaved → attorney_profiles_final.csv")
#     # https://www.jacobs-jacobs.com/
#     # https://csgtrials.com/
#     # https://www.perecman.com/
