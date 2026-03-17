import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
from urllib.parse import quote_plus
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

# Parameters
KEYWORDS = ["Gaza", "Hamas", "Palestine"]
DATE_RANGES = [
    ("2023-12-21", "2024-01-11"),
    ("2024-09-19", "2024-10-10")
]

# Setup Selenium
options = webdriver.ChromeOptions()
options.add_argument("--headless")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def date_in_range(date_str, start, end):
    """Check if date is in range."""
    try:
        pub_date = datetime.strptime(date_str[:10], '%Y-%m-%d')
        return datetime.strptime(start, "%Y-%m-%d") <= pub_date <= datetime.strptime(end, "%Y-%m-%d")
    except:
        return False

def search_guardian():
    print("Scraping The Guardian...")
    base_url = "https://www.theguardian.com"
    data = []

    for keyword in KEYWORDS:
        for start_date, end_date in DATE_RANGES:
            search_url = f"https://www.theguardian.com/world/palestinian-territories?q={quote_plus(keyword)}"
            response = requests.get(search_url)
            soup = BeautifulSoup(response.content, "html.parser")

            articles = soup.find_all("a", class_="fc-item__link")
            for link in articles:
                article_url = link['href']
                try:
                    page = requests.get(article_url)
                    page_soup = BeautifulSoup(page.content, "html.parser")
                    title = page_soup.find("title").text.strip()
                    date_meta = page_soup.find("meta", {"property": "article:published_time"})
                    pub_date = date_meta["content"] if date_meta else ""

                    if not date_in_range(pub_date, start_date, end_date):
                        continue

                    img = page_soup.find("figure")
                    img_url = ""
                    if img:
                        img_tag = img.find("img")
                        if img_tag and img_tag.has_attr('src'):
                            img_url = img_tag['src']
                    data.append({
                        "source": "The Guardian",
                        "date": pub_date,
                        "title": title,
                        "url": article_url,
                        "image": img_url
                    })
                except Exception as e:
                    print(f"Error with {article_url}: {e}")
    return data

def search_cnn():
    print("Scraping CNN with Selenium...")
    base_url = "https://edition.cnn.com/search?q={}&size=20&from={}&page={}&sort=newest"

    data = []
    for keyword in KEYWORDS:
        for start_date, end_date in DATE_RANGES:
            for page_num in range(1, 3):  # 2 pages per keyword/date
                search_url = base_url.format(quote_plus(keyword), 0, page_num)
                driver.get(search_url)
                time.sleep(3)
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                articles = soup.find_all("div", class_="container__headline")

                for article in articles:
                    a_tag = article.find("a")
                    if not a_tag:
                        continue
                    article_url = "https://edition.cnn.com" + a_tag['href']
                    try:
                        driver.get(article_url)
                        time.sleep(2)
                        article_soup = BeautifulSoup(driver.page_source, "html.parser")
                        title = article_soup.title.text.strip()
                        date_tag = article_soup.find("meta", {"itemprop": "datePublished"})
                        pub_date = date_tag["content"] if date_tag else ""
                        if not date_in_range(pub_date, start_date, end_date):
                            continue
                        img_tag = article_soup.find("img")
                        img_url = img_tag['src'] if img_tag else ""

                        data.append({
                            "source": "CNN",
                            "date": pub_date,
                            "title": title,
                            "url": article_url,
                            "image": img_url
                        })
                    except Exception as e:
                        print(f"Error on CNN article: {e}")
    return data

# Run both scrapers
guardian_data = search_guardian()
cnn_data = search_cnn()

# Merge and export
all_data = guardian_data + cnn_data
df = pd.DataFrame(all_data)
df.to_csv("conflict_images_metadata.csv", index=False)
print("✅ Exported to conflict_images_metadata.csv")

# Optional: PDF/HTML output
html_output = "<html><body>"
for entry in all_data:
    html_output += f"""
    <h3>{entry['title']}</h3>
    <p><b>Date:</b> {entry['date']}<br>
    <b>Source:</b> {entry['source']}<br>
    <a href="{entry['url']}" target="_blank">Read article</a></p>
    <img src="{entry['image']}" width="600"><hr>
    """
html_output += "</body></html>"

with open("conflict_images.html", "w", encoding="utf-8") as f:
    f.write(html_output)

print("✅ Created conflict_images.html for visual review.")

# Clean up
driver.quit()
