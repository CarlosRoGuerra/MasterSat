import requests
from bs4 import BeautifulSoup

def decode_message_from_doc(url):
    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    points = []

    for row in soup.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]

        if len(cells) != 3:
            continue

        if cells[0] == "x-coordinate":
            continue

        try:
            x = int(cells[0])
            char = cells[1]
            y = int(cells[2])
            points.append((x, y, char))
        except ValueError:
            continue

    if not points:
        print("Nenhum ponto encontrado.")
        return

    max_x = max(x for x, y, char in points)
    max_y = max(y for x, y, char in points)

    grid = [[" " for _ in range(max_x + 1)] for _ in range(max_y + 1)]

    for x, y, char in points:
        grid[y][x] = char

    for line in grid:
        print("".join(line))


url = "https://docs.google.com/document/d/e/2PACX-1vSvM5gDlNvt7npYHhp_XfsJvuntUhq184By5xO_pA4b_gCWeXb6dM6ZxwN8rE6S4ghUsCj2VKR21oEP/pub"

decode_message_from_doc(url)