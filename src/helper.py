from bs4 import BeautifulSoup 

def clean_html_content(raw_html: str) -> str:
    """
    Pulisce l'HTML rimuovendo script, stili e attributi inutili
    per renderlo digeribile da un LLM.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Rimuovi tag inutili per l'IA
    for element in soup(["script", "style", "svg", "meta", "link", "noscript"]):
        element.decompose()

    # 2. Rimuovi commenti (opzionale, ma utile)
    # (Lo fa spesso in automatico il parser, ma per sicurezza ci fidiamo del decompose sopra)

    # 3. Pulizia attributi: teniamo solo ID, Class, Name, Href e Type
    # Questo riduce drasticamente i token
    allowed_attrs = ['id', 'class', 'name', 'href', 'type', 'placeholder', 'aria-label', 'role']
    for tag in soup.find_all(True):
        attrs = dict(tag.attrs)
        for attr in attrs:
            if attr not in allowed_attrs:
                del tag.attrs[attr]

    # 4. Ritorna il testo formattato
    return soup.prettify()