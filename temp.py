# import csv, os
# from pathlib import Path

# file_path = "attorney_profiles/attorney_profiles_final_New Haven_Connecticut.csv"

# data = []

# folder_path = "attorney_profiles"

# all_data = []

# for file_path in Path(folder_path).rglob("*.csv"):
        
#     print(f"Reading: {file_path}")
    
#     with open(file_path, encoding="utf-8", errors="ignore") as f:
#         reader = csv.reader(f)
        
#         for row in reader:
#             data.append(row)

# # Print result
# email_set = set()
# consolidated_data = []
# for row in data:
#     if row[2] not in email_set:  # Assuming email is in the third column (index 2)
#         consolidated_data.append(row)
#     else:
#         print(f"Duplicate email found and skipped: {row[2]}")  # Optional: log duplicate emails
#     email_set.add(row[2])  # Assuming email is in the third column (index 2)

# header = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State"]

# with open("attorney_profiles_final_consolidated.csv", mode="w", encoding="utf-8", newline='') as file:
#     writer = csv.writer(file)
    
#     # Write header first
#     writer.writerow(header)
    
#     # Write existing rows
#     for row in consolidated_data:
#         writer.writerow(row)
    

# print("f")
# from email_verifier import EmailVerifier
# print("f")
# from email_verifier import EmailVerifier

# print(EmailVerifier().get_mx_records("brownandcrouppen.com"))
# def find_spf_includes(domain):
#     try:
#         import dns.resolver
#         answers = dns.resolver.resolve(domain, 'TXT')
#         for rdata in answers:
#             txt_record = str(rdata)
#             if "v=spf1" in txt_record:
#                 return txt_record # This will show all authorized domains
#     except:
#         return None
# print(find_spf_includes("getbc.com"))

import requests

def get_ms_tenant_id(domain):
    # This endpoint is public and returns the OpenID configuration for a domain
    url = f"https://login.microsoftonline.com/{domain}/.well-known/openid-configuration"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            # The 'issuer' field contains the Tenant ID
            # Example: https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0
            tenant_id = response.json().get('issuer').split('/')[-2]
            return tenant_id
    except:
        return None
    return None

import requests
import re

def search_for_email_domain(company_name):
    # We search specifically for the email pattern associated with the name
    query = f'"{company_name}" email'
    url = f"https://www.bing.com/search?q={query}" # Or use a Search API like Serper
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        # Regex to find anything that looks like an email domain in the search results
        # We look for common patterns like @get... or @[initials]...
        found_domains = re.findall(r'@([a-z0-9]+(?:\.[a-z0-9]+)+)', response.text)
        
        # Filter out common garbage like 'sentry.io', 'google.com', etc.
        ignored = ['microsoft.com', 'outlook.com', 'schema.org', 'bing.com']
        clean_domains = [d for d in found_domains if d not in ignored]
        
        return list(set(clean_domains))
    except:
        return []

# TEST
print(f"Social Discovery: {search_for_email_domain('Brown & Crouppen')}")

# # Example Usage:
# website_tid = get_ms_tenant_id("brownandcrouppen.com")
# email_tid = get_ms_tenant_id("getbc.com")

# if website_tid == email_tid and website_tid is not None:
#     print(f"Match! Both domains belong to Tenant: {website_tid}")


# import requests

# def verify_domain_match(test_domain, target_tenant_id):
#     # This is the "User Realm" discovery endpoint
#     url = f"https://login.microsoftonline.com/getuserrealm.srf?login=user@{test_domain}&json=1"
    
#     try:
#         response = requests.get(url, timeout=10)
#         data = response.json()
        
#         # This endpoint returns the Tenant ID associated with that domain
#         found_id = data.get("AccountType") # In some versions, it's 'tenant_id' or 'AccountType'
        
#         # If the Tenant ID matches your target GUID, you found the right domain
#         # NOTE: Many tools use 'https://azmap.dev/api/tenant?domain=' for this now
#         return data
#     except:
#         return None

# # Use this to verify if 'getbc.com' belongs to your ID
# result = verify_domain_match("brownandcrouppen.com", "0c3619b0-4ddf-44fd-bde8-ab08b425cd61")
# print(result)

from email_verifier import EmailVerifier
print(EmailVerifier().validate_syntax(EmailVerifier().generate_email_patterns("seth w slayden", "stjoelaw.com")))
