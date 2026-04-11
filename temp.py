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
    # writer = csv.writer(file)
    
    # # Write header first
    # writer.writerow(header)
    
    # # Write existing rows
    # for row in consolidated_data:
    #     writer.writerow(row)
    

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

from email_verifier import EmailVerifier
# print(EmailVerifier().get_mx_records("wasdenlawoffices.com"))
print(EmailVerifier().verify(["vishal.vr@fourkites.com", "rahul.r@fourkites.com"]))