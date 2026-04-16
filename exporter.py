import csv
import os


def export_csv(teachers: list[dict], school_info: dict, output_file: str):
    """Export enriched teacher data to CSV."""
    fieldnames = [
        "name", "email", "email_status", "role", "department",
        "phone", "linkedin_url", "bio",
        "school_name", "school_address", "school_city",
        "school_state", "school_zip", "school_phone", "source_url",
    ]

    s = school_info  # shorthand

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for t in teachers:
            writer.writerow({
                "name": t.get("name", ""),
                "email": t.get("email", ""),
                "email_status": t.get("email_status", ""),
                "role": t.get("role", ""),
                "department": t.get("department", ""),
                "phone": t.get("phone", ""),
                "linkedin_url": t.get("linkedin_url", ""),
                "bio": t.get("bio", ""),
                "school_name": s.get("school_name", ""),
                "school_address": s.get("address", ""),
                "school_city": s.get("city", ""),
                "school_state": s.get("state", ""),
                "school_zip": s.get("zip", ""),
                "school_phone": s.get("phone", ""),
                "source_url": t.get("source_url", ""),
            })

    print(f"\n[✓] Exported {len(teachers)} STEM teachers to "
          f"{os.path.abspath(output_file)}")
