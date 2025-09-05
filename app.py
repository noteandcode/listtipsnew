from flask import Flask, render_template, request, send_file, session
from urllib.parse import urlparse
from urllib import robotparser
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
import pandas as pd
import io

app = Flask(__name__)
app.secret_key = "supersecret"  # szükséges a session-höz


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
    links = []

    # Selenium beállítása headless Chrome-mal (Selenium Manager automatikusan kezeli a drivert)
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)  # nincs szükség driver path-ra
    driver.set_page_load_timeout(60)

    try:
        driver.get(url)
        # Várunk, amíg legalább egy <a> megjelenik (nem használunk time.sleep-et)
        WebDriverWait(driver, 15).until(lambda d: len(d.find_elements(By.TAG_NAME, "a")) > 0)

        # Stale element reference elkerülése: href értékeket gyűjtjük, nem az elemeket
        links_set = set()
        
        # Többszörös próbálkozás a dinamikus tartalom kezelésére
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Minden alkalommal frissen keressük meg az elemeket
                anchors = driver.find_elements(By.TAG_NAME, "a")
                
                # Egy menetben gyűjtjük ki az összes href értéket
                href_values = []
                for i, anchor in enumerate(anchors):
                    try:
                        href = anchor.get_attribute("href")
                        if href:
                            href_values.append(href)
                    except Exception as e:
                        # Ha egy elem elavult, folytatjuk a következővel
                        continue
                
                # Szűrjük és tároljuk az érvényes linkeket
                for href in href_values:
                    if href and href.startswith("http"):
                        links_set.add(href)
                
                # Ha sikerült, kilépünk a ciklusból
                break
                
            except Exception as e:
                if attempt == max_attempts - 1:
                    # Ha az utolsó próbálkozás is sikertelen, dobjuk tovább a hibát
                    raise e
                # Várunk egy kicsit a következő próbálkozás előtt
                import time
                time.sleep(1)
        
        links = list(links_set)
        
    finally:
        driver.quit()

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