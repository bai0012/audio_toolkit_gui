import requests
from bs4 import BeautifulSoup
import json # To convert dictionary to JSON string
import re   # For parsing cover image URL fallback
from datetime import datetime # For date formatting

def scrape_vgmdb_album_json(album_id):
    """
    Scrapes album information (including multiple languages) from a VGMdb album page
    and returns it as a JSON string. Only outputs JSON.

    Args:
        album_id (int or str): The VGMdb album ID.

    Returns:
        str: A JSON string containing the scraped album information,
             or None if the page cannot be fetched or parsed significantly.
             Returns JSON with partially scraped data on parsing errors.
    """
    url = f"https://vgmdb.net/album/{album_id}"
    headers = {
        # Setting a User-Agent is polite and sometimes necessary
        'User-Agent': 'MyVGMdbMultiLangScraper/1.2 (check source repo if applicable)'
    }
    album_data = {
        "id": str(album_id),
        "url": url,
        "titles": {}, # Store multiple titles {lang_code: title}
        "cover_image": None,
        "catalog_number": None,
        "release_date": None, # Will be formatted to YYYYMMDD
        "publish_format": None,
        "classification": None,
        "organizations": { # Store multiple names {lang_code: name}
            "labels": [],
            "manufacturers": [],
            "distributors": []
        },
        "tracklist": [], # List of track dictionaries
                         # Each track dict: {'number': str, 'titles': {lang: str}, 'duration': str}
        "notes": None, # Will preserve line breaks
        "related_products": [], # Store related products {lang_code: name}
        "platforms": [], # Store platform names
    }

    # --- Fetch HTML ---
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding # Ensure correct encoding
        # print(f"Successfully fetched HTML for {album_id}.") # DEBUG PRINT - REMOVED

    except requests.exceptions.RequestException as e:
        # print(f"Error fetching URL {url}: {e}") # DEBUG PRINT - REMOVED
        # Still return the basic structure with the ID and URL if fetch fails
        return json.dumps(album_data, indent=4, ensure_ascii=False)

    # --- Parse HTML ---
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
        # print(f"Parsing HTML for {album_id}...") # DEBUG PRINT - REMOVED

        # --- Album Titles (Multi-language) ---
        title_h1 = soup.find('h1')
        if title_h1:
            title_spans = title_h1.find_all('span', class_='albumtitle')
            for span in title_spans:
                lang = span.get('lang', 'unknown')
                title_text = span.get_text(strip=True)
                if '/' in title_text and lang != 'en':
                    title_text = title_text.split('/')[-1].strip()
                if title_text:
                    album_data['titles'][lang] = title_text

        # --- Cover Image (using OpenGraph meta tag, with fallback) ---
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            album_data['cover_image'] = og_image['content']
        else:
            cover_div = soup.find('div', id='coverart')
            if cover_div and 'style' in cover_div.attrs:
                style = cover_div['style']
                match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
                if match:
                    album_data['cover_image'] = match.group(1)

        # --- Info Table Data (Main info section) ---
        info_table = soup.find('table', id='album_infobit_large')
        if info_table:
            rows = info_table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 2:
                    label_cell = cells[0]
                    value_cell = cells[1]

                    label_span = label_cell.find('span', class_='label')
                    label_text = label_span.get_text(strip=True).lower() if label_span else label_cell.get_text(strip=True).lower()

                    def get_org_names(cell):
                        names_list = []
                        links = cell.find_all('a')
                        for link in links:
                            org_names = {}
                            name_spans = link.find_all('span', class_='productname')
                            if name_spans:
                                for span in name_spans:
                                    lang = span.get('lang', 'unknown')
                                    name_text = span.get_text(strip=True)
                                    if name_text:
                                         org_names[lang] = name_text
                            else:
                                 name_text = link.get_text(strip=True)
                                 if name_text:
                                     org_names['unknown'] = name_text
                            if org_names:
                                names_list.append(org_names)
                        return names_list

                    if "catalog number" in label_text:
                        album_data['catalog_number'] = value_cell.get_text(strip=True)
                    elif "release date" in label_text:
                        date_text = ""
                        link = value_cell.find('a')
                        if link:
                            date_text = link.get_text(strip=True)
                        else:
                             date_text = value_cell.get_text(strip=True)

                        # Format date to YYYYMMDD
                        if date_text:
                            try:
                                # Attempt to parse known format Month Day, Year
                                dt_obj = datetime.strptime(date_text, "%b %d, %Y")
                                album_data['release_date'] = dt_obj.strftime("%Y%m%d")
                            except ValueError:
                                # If parsing fails, keep original text or set to None/error
                                album_data['release_date'] = date_text # Keep original if format unknown
                                # print(f"Warning: Could not parse date '{date_text}' into YYYYMMDD for album {album_id}") # DEBUG PRINT - REMOVED
                    elif "publish format" in label_text:
                        album_data['publish_format'] = value_cell.get_text(strip=True)
                    elif "classification" in label_text:
                        album_data['classification'] = value_cell.get_text(strip=True)
                    elif "label" in label_text:
                        album_data['organizations']['labels'] = get_org_names(value_cell)
                    elif "manufacturer" in label_text:
                         album_data['organizations']['manufacturers'] = get_org_names(value_cell)
                    elif "distributor" in label_text:
                         album_data['organizations']['distributors'] = get_org_names(value_cell)

        # --- Tracklist (Multi-language) ---
        tracklist_nav = soup.find('ul', id='tlnav')
        tracklist_div = soup.find('div', id='tracklist')
        lang_tabs = {} # {lang_name: target_id}

        if tracklist_nav:
            links = tracklist_nav.find_all('a')
            for link in links:
                lang_name = link.get_text(strip=True).lower() # Normalize lang name
                rel_id = link.get('rel')
                if lang_name and rel_id:
                    lang_tabs[lang_name] = rel_id

        if tracklist_div and lang_tabs:
            primary_lang_name = next(iter(lang_tabs))
            primary_lang_id = lang_tabs[primary_lang_name]
            primary_tracklist_span = tracklist_div.find('span', id=primary_lang_id)

            if primary_tracklist_span:
                primary_track_table = primary_tracklist_span.find('table', class_='role')
                if primary_track_table:
                    track_rows = primary_track_table.find_all('tr', class_='rolebit')
                    for track_row in track_rows:
                        cells = track_row.find_all('td')
                        if len(cells) >= 3:
                            track_num_text = cells[0].get_text(strip=True)
                            primary_title = cells[1].get_text(strip=True)
                            time_span = cells[-1].find('span', class_='time')
                            track_time = time_span.get_text(strip=True) if time_span else 'N/A'

                            track_entry = {
                                'number': track_num_text,
                                'titles': {primary_lang_name: primary_title},
                                'duration': track_time
                            }
                            album_data['tracklist'].append(track_entry)

            for lang_name, lang_id in lang_tabs.items():
                if lang_name == primary_lang_name:
                    continue

                lang_tracklist_span = tracklist_div.find('span', id=lang_id)
                if lang_tracklist_span:
                    lang_track_table = lang_tracklist_span.find('table', class_='role')
                    if lang_track_table:
                        track_rows = lang_track_table.find_all('tr', class_='rolebit')
                        for i, track_row in enumerate(track_rows):
                             if i < len(album_data['tracklist']):
                                cells = track_row.find_all('td')
                                if len(cells) >= 2:
                                    lang_title = cells[1].get_text(strip=True)
                                    if lang_title:
                                        album_data['tracklist'][i]['titles'][lang_name] = lang_title

        # --- Notes (Preserve Line Breaks) ---
        notes_div = soup.find('div', id='notes')
        if notes_div:
             # get_text with separator='\n' interprets <br> as newline.
             # strip=True removes leading/trailing whitespace from the whole block.
             album_data['notes'] = notes_div.get_text(separator='\n', strip=True)


        # --- Right Column Data (Album Stats) ---
        right_column = soup.find('td', id='rightcolumn')
        if right_column:
            product_heading = right_column.find(lambda tag: tag.name == 'b' and 'products represented' in tag.get_text(strip=True).lower())
            if product_heading:
                product_div = product_heading.parent
                if product_div:
                    product_links = product_div.find_all('a')
                    for link in product_links:
                         prod_names = {}
                         name_spans = link.find_all('span', class_='productname')
                         if name_spans:
                            for span in name_spans:
                                lang = span.get('lang', 'unknown')
                                name_text = span.get_text(strip=True)
                                if name_text:
                                     prod_names[lang] = name_text
                         else:
                            name_text = link.get_text(strip=True)
                            if name_text:
                                prod_names['unknown'] = name_text
                         if prod_names:
                            album_data['related_products'].append(prod_names)

            platform_heading = right_column.find(lambda tag: tag.name == 'b' and 'platforms represented' in tag.get_text(strip=True).lower())
            if platform_heading:
                 platform_div = platform_heading.parent
                 if platform_div:
                     platforms_text = platform_div.get_text(separator='\n', strip=True)
                     lines = [line.strip() for line in platforms_text.split('\n') if line.strip()]
                     if len(lines) > 1:
                        # Find index of heading to slice after it
                        try:
                            heading_index = lines.index(platform_heading.get_text(strip=True))
                            album_data['platforms'] = lines[heading_index+1:]
                        except ValueError: # Fallback if heading text not found directly
                             album_data['platforms'] = lines[1:]

        # print(f"Parsing complete for {album_id}.") # DEBUG PRINT - REMOVED
        return json.dumps(album_data, indent=4, ensure_ascii=False)

    except Exception as e:
        # print(f"Error parsing HTML for album {album_id}: {e}") # DEBUG PRINT - REMOVED
        # Return partially scraped data as JSON even on parsing error
        # print("Returning partially scraped data due to parsing error.") # DEBUG PRINT - REMOVED
        return json.dumps(album_data, indent=4, ensure_ascii=False)


# --- Main Execution ---
if __name__ == "__main__":
    import sys # To get command line arguments

    if len(sys.argv) < 2:
        # print("Usage: python your_script_name.py <album_id>") # DEBUG PRINT - REMOVED
        # Output minimal JSON error if no ID provided
        error_output = json.dumps({"error": "No album ID provided.", "id": None}, indent=4)
        print(error_output)
        sys.exit(1)

    target_album_id = sys.argv[1]
    # Validate if ID looks like a number, though VGMdb IDs are numeric
    if not target_album_id.isdigit():
         error_output = json.dumps({"error": "Invalid album ID provided.", "id": target_album_id}, indent=4)
         print(error_output)
         sys.exit(1)

    json_output = scrape_vgmdb_album_json(target_album_id)

    # Print the final JSON output - this is the only intended stdout output
    print(json_output)