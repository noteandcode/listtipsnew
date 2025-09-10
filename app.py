import io
from urllib.parse import urlparse
from urllib import robotparser
import time

import pandas as pd
from flask import Flask, render_template, request, send_file, session
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException

app = Flask(__name__)
app.secret_key = "supersecret" 

def create_driver():
    """Create a Chrome driver with optimized settings to prevent crashes"""
    options = webdriver.ChromeOptions()
    
    # Basic headless settings
    options.add_argument('--headless=new')  # Use new headless mode
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    
    # Memory and stability improvements
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-images')  # Don't load images to save memory
    options.add_argument('--disable-javascript')  # Disable JS if not needed for link extraction
    options.add_argument('--memory-pressure-off')
    options.add_argument('--max_old_space_size=4096')
    
    # Process management
    options.add_argument('--single-process')  # Use single process to avoid crashes
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--disable-backgrounding-occluded-windows')
    
    # Network and security
    options.add_argument('--disable-web-security')
    options.add_argument('--disable-features=TranslateUI')
    options.add_argument('--disable-ipc-flooding-protection')
    
    # User agent
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    # Set page load strategy to eager (don't wait for all resources)
    options.page_load_strategy = 'eager'
    
    return webdriver.Chrome(options=options)


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


def extract_links(url):
    """Extract links from a webpage with improved error handling and crash prevention"""
    links = []
    driver = None
    
    try:
        driver = create_driver()
        # Reduced timeout to prevent hanging
        driver.set_page_load_timeout(30)
        driver.implicitly_wait(5)
        
        print(f"Attempting to load URL: {url}")
        driver.get(url)
        
        # Wait for page to load with shorter timeout
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            print("Page load timeout, but continuing with link extraction...")
        
        # Alternative wait: check for any links to appear
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.TAG_NAME, "a")) > 0
            )
        except TimeoutException:
            print("No links found within timeout, but continuing...")
        
        # Extract links with improved error handling
        links_set = set()
        max_attempts = 2  # Reduced attempts to prevent hanging
        
        for attempt in range(max_attempts):
            try:
                print(f"Link extraction attempt {attempt + 1}")
                
                # Get all anchor elements at once
                anchors = driver.find_elements(By.TAG_NAME, "a")
                print(f"Found {len(anchors)} anchor elements")
                
                # Extract href attributes in batches to prevent stale element references
                batch_size = 50  # Process in smaller batches
                for i in range(0, len(anchors), batch_size):
                    batch = anchors[i:i + batch_size]
                    
                    for anchor in batch:
                        try:
                            href = anchor.get_attribute("href")
                            if href and href.startswith("http"):
                                links_set.add(href)
                        except Exception as e:
                            # Skip stale or problematic elements
                            continue
                    
                    # Small delay between batches to prevent overloading
                    if i + batch_size < len(anchors):
                        time.sleep(0.1)
                
                # If we got here successfully, break the retry loop
                break
                
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_attempts - 1:
                    # Last attempt failed, but continue with what we have
                    print("All attempts failed, using collected links so far")
                    break
                # Wait before retry
                time.sleep(2)
        
        links = list(links_set)
        print(f"Successfully extracted {len(links)} unique links")
        
    except WebDriverException as e:
        print(f"WebDriver error: {str(e)}")
        raise Exception(f"Browser error occurred: {str(e)}")
    except Exception as e:
        print(f"General error during link extraction: {str(e)}")
        raise Exception(f"Error during link extraction: {str(e)}")
    finally:
        # Ensure driver is always closed
        if driver:
            try:
                driver.quit()
            except Exception as e:
                print(f"Error closing driver: {str(e)}")
    
    return links


def _is_same_or_subdomain(host: str, base_host: str) -> bool:
    """Igaz, ha a host megegyezik a base_host-tal vagy annak aldomainje."""
    host = (host or "").lower()
    base_host = (base_host or "").lower()
    return host == base_host or (base_host and host.endswith("." + base_host))


def _is_related_domain(host: str, base_host: str) -> bool:
    """
    Igaz, ha a host kapcsolódik a base_host-hoz bármilyen módon:
    - Megegyezik vele
    - Aldomainje
    - Tartalmazza mint substring-et (pl. valamioldal.aloldal.hu tartalmazza a valamioldal.hu-t)
    """
    host = (host or "").lower()
    base_host = (base_host or "").lower()
    
    if not host or not base_host:
        return False
    
    # 1. Egyezés vagy aldomain ellenőrzés (eredeti logika)
    if host == base_host or host.endswith("." + base_host):
        return True
    
    # 2. Új: ellenőrizzük, hogy a host tartalmazza-e a base_host-ot
    # Például: valamioldal.aloldal.hu tartalmazza a valamioldal.hu-t
    if base_host in host:
        return True
    
    # 3. Fordított eset: ellenőrizzük, hogy a base_host tartalmazza-e a host-ot
    # Például: ha base_host = sub.example.com és host = example.com
    if host in base_host:
        return True
    
    return False


def filter_links(links, base_url):
    """
    - Csak a gyökér (/) hivatkozásokat tartjuk meg (pl. https://valami.hu/)
    - Kizárjuk a saját domaint és aldomainjeit
    - Kizárjuk azokat a linkeket is, amelyek *tartalmazzák* a beírt URL-t (share, redirect linkek)
    - ÚJ: Kizárjuk azokat a domaineket is, amelyek kapcsolódnak az eredeti domainhez
    - Duplikátumok eltávolítása
    """
    base_url_norm = (base_url or "").rstrip("/")
    parsed_base = urlparse(base_url_norm)
    base_host = (parsed_base.hostname or "").lower()

    filtered = set()
    for link in links:
        if not link or not link.startswith("http"):
            continue

        # (1) Kizárás: ha a teljes link sztringben benne van az eredeti URL (kért extra feltétel)
        if base_url_norm and base_url_norm in link:
            continue

        parsed = urlparse(link)
        host = (parsed.hostname or "").lower()

        # (2) ÚJ: Kizárás kapcsolódó domainek (beleértve az aloldalakat is)
        if _is_related_domain(host, base_host):
            continue

        # (3) Csak a gyökéroldalt engedjük át
        path = (parsed.path or "").strip("/")
        if path == "":
            filtered.add(f"{parsed.scheme}://{parsed.netloc}/")

    # Rendezetten térünk vissza a determinisztikus kimenetért
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
        if url and is_allowed(url):
            try:
                links = extract_links(url)
                results = filter_links(links, url)
                session["results"] = results
                if not results:
                    no_results = True
            except Exception as e:
                error = f"Error extracting links: {e}"
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
