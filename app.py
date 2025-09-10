import io
from urllib.parse import urlparse
from urllib import robotparser
import requests
from bs4 import BeautifulSoup

import pandas as pd
from flask import Flask, render_template, request, send_file, session

app = Flask(__name__)
app.secret_key = "supersecret" 

def is_allowed(url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch("*", url)
    except Exception:
        return False

def extract_links_simple(url):
    """Simple link extraction using requests and BeautifulSoup"""
    links = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Quick request with timeout
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find all links
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith('http'):
                links.append(href)
        
        return list(set(links))  # Remove duplicates
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return []

def _is_related_domain(host: str, base_host: str) -> bool:
    """Domain relation check"""
    host = (host or "").lower()
    base_host = (base_host or "").lower()
    
    if not host or not base_host:
        return False
    
    if host == base_host or host.endswith("." + base_host):
        return True
    
    if base_host in host or host in base_host:
        return True
    
    return False

def filter_links(links, base_url):
    """Filter links to keep only root domains"""
    base_url_norm = (base_url or "").rstrip("/")
    parsed_base = urlparse(base_url_norm)
    base_host = (parsed_base.hostname or "").lower()

    filtered = set()
    for link in links:
        if not link or not link.startswith("http"):
            continue

        if base_url_norm and base_url_norm in link:
            continue

        parsed = urlparse(link)
        host = (parsed.hostname or "").lower()

        if _is_related_domain(host, base_host):
            continue

        path = (parsed.path or "").strip("/")
        if path == "":
            filtered.add(f"{parsed.scheme}://{parsed.netloc}/")

    return sorted(filtered)

@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    error = None
    no_results = False
    
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if url and not url.startswith("http"):
            url = "http://" + url
        
        if not url:
            error = "Please provide a valid URL"
        elif is_allowed(url):
            try:
                links = extract_links_simple(url)
                results = filter_links(links, url)
                session["results"] = results
                if not results:
                    no_results = True
            except Exception as e:
                error = f"Error extracting links: {str(e)[:100]}"
        else:
            error = "Scraping not allowed by robots.txt"
    
    return render_template("index.html", results=results, error=error, no_results=no_results)

@app.route("/download")
def download():
    results = session.get("results")
    if not results:
        return "Nincs exportálható eredmény."

    df = pd.DataFrame(results, columns=["Linkek"])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Eredmények")
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="talalatok.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

if __name__ == "__main__":
    app.run(debug=True)
