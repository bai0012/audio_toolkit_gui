# audio_toolkit/vgmdb_scraper.py
import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
from typing import Dict, Optional, Any, List


def _parse_date(date_text: str) -> Optional[str]:
    """Attempts to parse date text into YYYYMMDD format."""
    if not date_text:
        return None
    try:
        # Prioritize full date format
        dt_obj = datetime.strptime(date_text, "%b %d, %Y")
        return dt_obj.strftime("%Y%m%d")
    except ValueError:
        try:
            # Try YYYY-MM-DD
            dt_obj = datetime.strptime(date_text, "%Y-%m-%d")
            return dt_obj.strftime("%Y%m%d")
        except ValueError:
             try:
                 # Try YYYY.MM.DD
                 dt_obj = datetime.strptime(date_text, "%Y.%m.%d")
                 return dt_obj.strftime("%Y%m%d")
             except ValueError:
                 try:
                      # Try just YYYY
                      dt_obj = datetime.strptime(date_text, "%Y")
                      return dt_obj.strftime("%Y") # Return just YYYY if only year found
                 except ValueError:
                      # Fallback: Keep original if format unknown/unparsable
                      return date_text

def _get_preferred_lang(data_dict: Dict[str, str],
                       langs_preference: List[str] = ['ja', 'ja-Latn', 'en']) -> Optional[str]:
    """Gets the value from a dict based on preferred language keys."""
    if not data_dict:
        return None
    for lang in langs_preference:
        if lang in data_dict and data_dict[lang]:
            return data_dict[lang]
    # Fallback: return the first value found if preferred langs not present
    try:
        first_key = next(iter(data_dict))
        return data_dict[first_key]
    except StopIteration:
        return None # Empty dictionary

def scrape_vgmdb_album(album_id: str, logger_func=print) -> Optional[Dict[str, Any]]:
    """
    Scrapes album information from a VGMdb album page.

    Args:
        album_id (str): The VGMdb album ID.
        logger_func (callable): Function to use for logging messages.

    Returns:
        dict or None: A dictionary containing the scraped album information,
                      or None if the page cannot be fetched. Returns partial
                      dict if parsing errors occur after fetch.
    """
    url = f"https://vgmdb.net/album/{album_id}"
    headers = {'User-Agent': 'AudioToolkitGUI/1.0'}
    album_data = {
        "id": str(album_id),
        "url": url,
        "titles": {},
        "cover_image": None,
        "catalog_number": None,
        "release_date": None, # Raw text initially
        "publish_format": None,
        "classification": None,
        "organizations": {"labels": [], "publishers": [], "manufacturers": [], "distributors": []}, # Added publishers
        "tracklist": [],
        "notes": None,
        "related_products": [],
        "platforms": [],
        "_error": None # Internal field for reporting errors
    }

    # --- Fetch HTML ---
    try:
        logger_func(f"Fetching VGMdb data for ID: {album_id} from {url}")
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        logger_func("  Successfully fetched HTML.")
    except requests.exceptions.RequestException as e:
        logger_func(f"  [!] Error fetching URL {url}: {e}")
        album_data["_error"] = f"Network error: {e}"
        return album_data # Return basic structure with error

    # --- Parse HTML ---
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
        logger_func("  Parsing HTML...")

        # --- Album Titles ---
        title_h1 = soup.find('h1')
        if title_h1:
            title_spans = title_h1.find_all('span', class_='albumtitle')
            for span in title_spans:
                lang = span.get('lang', 'unknown').lower() # Normalize lang
                title_text = span.get_text(strip=True)
                # Simple split logic (might need refinement for complex cases)
                if '/' in title_text and lang != 'en':
                    parts = [p.strip() for p in title_text.split('/') if p.strip()]
                    if parts: title_text = parts[-1] # Assume last part is target lang
                if title_text:
                    album_data['titles'][lang] = title_text

        # --- Cover Image ---
        og_image = soup.find('meta', property='og:image')
        cover_url = None
        if og_image and og_image.get('content'):
            cover_url = og_image['content']
        else: # Fallback logic
            cover_div = soup.find('div', id='coverart')
            if cover_div and 'style' in cover_div.attrs:
                match = re.search(r"url\(['\"]?(.*?)['\"]?\)", cover_div['style'])
                if match: cover_url = match.group(1)

        # Remove "medium-" prefix if present
        if cover_url and "/medium-" in cover_url:
             album_data['cover_image'] = cover_url.replace("/medium-", "/")
             logger_func(f"  Found and cleaned cover URL: {album_data['cover_image']}")
        elif cover_url:
             album_data['cover_image'] = cover_url
             logger_func(f"  Found cover URL: {cover_url}")
        else:
             logger_func("  Cover image URL not found.")


        # --- Info Table Data ---
        info_table = soup.find('table', id='album_infobit_large')
        if info_table:
            rows = info_table.find_all('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) == 2:
                    label_cell = cells[0]
                    value_cell = cells[1]
                    label_text = label_cell.get_text(strip=True).lower()

                    def get_org_names(cell): # Helper for org names
                        names_list = []
                        for link in cell.find_all('a'):
                            org_names = {}
                            for span in link.find_all('span', class_='productname'):
                                lang = span.get('lang', 'unknown').lower()
                                name = span.get_text(strip=True)
                                if name: org_names[lang] = name
                            if not org_names: # Fallback if no spans
                                name = link.get_text(strip=True)
                                if name: org_names['unknown'] = name
                            if org_names: names_list.append(org_names)
                        return names_list

                    if "catalog number" in label_text:
                        album_data['catalog_number'] = value_cell.get_text(strip=True)
                    elif "release date" in label_text:
                        link = value_cell.find('a')
                        album_data['release_date'] = link.get_text(strip=True) if link else value_cell.get_text(strip=True)
                    elif "publish format" in label_text:
                        album_data['publish_format'] = value_cell.get_text(strip=True)
                    elif "classification" in label_text:
                        album_data['classification'] = value_cell.get_text(strip=True)
                    elif "published by" in label_text: # Added publisher
                        album_data['organizations']['publishers'] = get_org_names(value_cell)
                    elif "label" in label_text: # Should be specific
                        album_data['organizations']['labels'] = get_org_names(value_cell)
                    elif "manufacturer" in label_text:
                         album_data['organizations']['manufacturers'] = get_org_names(value_cell)
                    elif "distributor" in label_text:
                         album_data['organizations']['distributors'] = get_org_names(value_cell)

        # --- Tracklist ---
        tracklist_nav = soup.find('ul', id='tlnav')
        tracklist_div = soup.find('div', id='tracklist')
        lang_tabs = {}
        if tracklist_nav:
            for link in tracklist_nav.find_all('a'):
                lang_name = link.get_text(strip=True).lower()
                rel_id = link.get('rel')
                if lang_name and rel_id: lang_tabs[lang_name] = rel_id

        if tracklist_div and lang_tabs:
            primary_lang_name = next(iter(lang_tabs), None)
            if primary_lang_name:
                primary_lang_id = lang_tabs[primary_lang_name]
                primary_span = tracklist_div.find('span', id=primary_lang_id)
                if primary_span:
                    primary_table = primary_span.find('table', class_='role')
                    if primary_table:
                        for row in primary_table.find_all('tr', class_='rolebit'):
                            cells = row.find_all('td')
                            if len(cells) >= 3:
                                num = cells[0].get_text(strip=True)
                                title = cells[1].get_text(strip=True)
                                time_span = cells[-1].find('span', class_='time')
                                duration = time_span.get_text(strip=True) if time_span else 'N/A'
                                album_data['tracklist'].append({
                                    'number': num,
                                    'titles': {primary_lang_name: title},
                                    'duration': duration
                                })
                # Add other languages
                for lang_name, lang_id in lang_tabs.items():
                    if lang_name == primary_lang_name: continue
                    lang_span = tracklist_div.find('span', id=lang_id)
                    if lang_span:
                        lang_table = lang_span.find('table', class_='role')
                        if lang_table:
                            for i, row in enumerate(lang_table.find_all('tr', class_='rolebit')):
                                 if i < len(album_data['tracklist']):
                                    cells = row.find_all('td')
                                    if len(cells) >= 2:
                                        title = cells[1].get_text(strip=True)
                                        if title: album_data['tracklist'][i]['titles'][lang_name] = title

        # --- Notes ---
        notes_div = soup.find('div', id='notes')
        if notes_div: album_data['notes'] = notes_div.get_text(separator='\n', strip=True)

        # --- Right Column (Products, Platforms) ---
        right_column = soup.find('td', id='rightcolumn')
        if right_column:
            # Products
            product_heading = right_column.find(lambda t: t.name == 'b' and 'products represented' in t.get_text(strip=True).lower())
            if product_heading and product_heading.parent:
                 for link in product_heading.parent.find_all('a'):
                      prod_names = {}
                      for span in link.find_all('span', class_='productname'):
                          lang = span.get('lang', 'unknown').lower()
                          name = span.get_text(strip=True); name and prod_names.setdefault(lang, name)
                      if not prod_names: name = link.get_text(strip=True); name and prod_names.setdefault('unknown', name)
                      if prod_names: album_data['related_products'].append(prod_names)
            # Platforms
            platform_heading = right_column.find(lambda t: t.name == 'b' and 'platforms represented' in t.get_text(strip=True).lower())
            if platform_heading and platform_heading.parent:
                 text_block = platform_heading.parent.get_text(separator='\n', strip=True)
                 lines = [line.strip() for line in text_block.split('\n') if line.strip()]
                 try: heading_idx = lines.index(platform_heading.get_text(strip=True)); album_data['platforms'] = lines[heading_idx+1:]
                 except ValueError: album_data['platforms'] = lines[1:] # Fallback

        logger_func("  Parsing complete.")
        return album_data

    except Exception as e:
        logger_func(f"  [!] Error parsing HTML for album {album_id}: {e}")
        album_data["_error"] = f"Parsing error: {e}"
        return album_data # Return partially scraped data


# Example usage (for testing the module directly)
if __name__ == "__main__":
    import sys
    def test_logger(msg): print(msg) # Simple console logger for testing

    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("Usage: python vgmdb_scraper.py <album_id>")
        sys.exit(1)

    target_album_id = sys.argv[1]
    scraped_data = scrape_vgmdb_album(target_album_id, test_logger)

    if scraped_data:
        # Pretty print the resulting dictionary as JSON
        print(json.dumps(scraped_data, indent=4, ensure_ascii=False))
    else:
        print(f"Failed to fetch data for album ID: {target_album_id}")