"""Location parsing and commute estimates from Pilsen.

Coordinates are approximate neighborhood / town centers. Commute minutes are
rough door-to-door CTA/Metra estimates, not routed trips: places listed with an
explicit minute value use a hand estimate; everything else uses a distance
formula. Treat every number here as a ballpark for sorting, not a promise.
"""
import math
import re

# name -> (lat, lon, minutes_or_None, kind)
# kind: "city" = inside Chicago (dense CTA), "suburb" = outside the city.
PLACES = {
    # --- Chicago neighborhoods / districts ---
    "pilsen": (41.8564, -87.6597, 8, "city"),
    "lower west side": (41.8564, -87.6597, 8, "city"),
    "the loop": (41.8837, -87.6289, 20, "city"),
    "loop": (41.8837, -87.6289, 20, "city"),
    "downtown": (41.8837, -87.6289, 20, "city"),
    "west loop": (41.8825, -87.6475, 20, "city"),
    "fulton market": (41.8866, -87.6520, 25, "city"),
    "river north": (41.8924, -87.6341, 30, "city"),
    "streeterville": (41.8927, -87.6200, 35, "city"),
    "magnificent mile": (41.8950, -87.6244, 35, "city"),
    "gold coast": (41.9047, -87.6278, 35, "city"),
    "old town": (41.9112, -87.6380, 35, "city"),
    "near north": (41.9000, -87.6340, 32, "city"),
    "south loop": (41.8587, -87.6266, 20, "city"),
    "printers row": (41.8730, -87.6290, 20, "city"),
    "museum campus": (41.8663, -87.6170, 30, "city"),
    "illinois medical district": (41.8693, -87.6745, 15, "city"),
    "medical district": (41.8693, -87.6745, 15, "city"),
    "near west side": (41.8693, -87.6745, 15, "city"),
    "university village": (41.8650, -87.6560, 15, "city"),
    "uic": (41.8708, -87.6505, 15, "city"),
    "united center": (41.8807, -87.6742, 20, "city"),
    "chinatown": (41.8517, -87.6338, 20, "city"),
    "bridgeport": (41.8381, -87.6511, 20, "city"),
    "mckinley park": (41.8317, -87.6732, 18, "city"),
    "little village": (41.8448, -87.7120, 18, "city"),
    "south lawndale": (41.8448, -87.7120, 18, "city"),
    "north lawndale": (41.8600, -87.7180, 25, "city"),
    "back of the yards": (41.8090, -87.6600, 25, "city"),
    "brighton park": (41.8190, -87.6990, 25, "city"),
    "bronzeville": (41.8169, -87.6170, 30, "city"),
    "douglas": (41.8350, -87.6180, 25, "city"),
    "hyde park": (41.7943, -87.5907, 45, "city"),
    "kenwood": (41.8096, -87.5937, 45, "city"),
    "woodlawn": (41.7800, -87.5960, 50, "city"),
    "south shore": (41.7614, -87.5756, 55, "city"),
    "englewood": (41.7798, -87.6446, 40, "city"),
    "chatham": (41.7410, -87.6127, 50, "city"),
    "beverly": (41.7146, -87.6716, 60, "city"),
    "garfield ridge": (41.7868, -87.7522, 40, "city"),
    "midway": (41.7868, -87.7522, 35, "city"),
    "west town": (41.8960, -87.6720, 30, "city"),
    "ukrainian village": (41.8990, -87.6860, 30, "city"),
    "east garfield park": (41.8807, -87.7020, 25, "city"),
    "garfield park": (41.8807, -87.7217, 30, "city"),
    "humboldt park": (41.9025, -87.7209, 40, "city"),
    "austin": (41.8940, -87.7650, 45, "city"),
    "wicker park": (41.9088, -87.6796, 35, "city"),
    "bucktown": (41.9217, -87.6796, 40, "city"),
    "logan square": (41.9233, -87.7083, 40, "city"),
    "lincoln park": (41.9214, -87.6513, 40, "city"),
    "lakeview": (41.9434, -87.6553, 50, "city"),
    "lake view": (41.9434, -87.6553, 50, "city"),
    "wrigleyville": (41.9484, -87.6553, 50, "city"),
    "north center": (41.9540, -87.6790, 50, "city"),
    "ravenswood": (41.9700, -87.6740, 55, "city"),
    "lincoln square": (41.9690, -87.6890, 55, "city"),
    "uptown": (41.9665, -87.6533, 55, "city"),
    "edgewater": (41.9833, -87.6600, 60, "city"),
    "andersonville": (41.9800, -87.6680, 60, "city"),
    "rogers park": (42.0106, -87.6696, 70, "city"),
    "albany park": (41.9683, -87.7249, 55, "city"),
    "irving park": (41.9530, -87.7360, 55, "city"),
    "portage park": (41.9580, -87.7650, 60, "city"),
    "jefferson park": (41.9705, -87.7600, 60, "city"),
    "o'hare": (41.9742, -87.9073, 65, "city"),
    "ohare": (41.9742, -87.9073, 65, "city"),
    # --- Suburbs (Metra BNSF stops at Halsted St in Pilsen, so its towns get hand estimates) ---
    "cicero": (41.8456, -87.7539, 25, "suburb"),
    "berwyn": (41.8506, -87.7937, 32, "suburb"),
    "riverside": (41.8350, -87.8228, 40, "suburb"),
    "north riverside": (41.8428, -87.8231, 45, "suburb"),
    "brookfield": (41.8239, -87.8517, 45, "suburb"),
    "la grange": (41.8050, -87.8692, 45, "suburb"),
    "lagrange": (41.8050, -87.8692, 45, "suburb"),
    "western springs": (41.8095, -87.9006, 50, "suburb"),
    "hinsdale": (41.8009, -87.9370, 55, "suburb"),
    "westmont": (41.7959, -87.9756, 60, "suburb"),
    "downers grove": (41.8089, -88.0112, 60, "suburb"),
    "lisle": (41.8011, -88.0748, 65, "suburb"),
    "naperville": (41.7508, -88.1535, 75, "suburb"),
    "aurora": (41.7606, -88.3201, 95, "suburb"),
    "oak park": (41.8850, -87.7845, 40, "suburb"),
    "river forest": (41.8978, -87.8140, 50, "suburb"),
    "forest park": (41.8795, -87.8137, 45, "suburb"),
    "maywood": (41.8792, -87.8431, 55, "suburb"),
    "melrose park": (41.9006, -87.8567, 65, "suburb"),
    "evanston": (42.0451, -87.6877, 75, "suburb"),
    "skokie": (42.0324, -87.7416, 75, "suburb"),
    "oak lawn": (41.7109, -87.7581, 55, "suburb"),
    "evergreen park": (41.7206, -87.7017, 55, "suburb"),
    "bedford park": (41.7681, -87.8131, None, "suburb"),
    "summit": (41.7881, -87.8106, None, "suburb"),
    "elmwood park": (41.9211, -87.8092, None, "suburb"),
    "elmhurst": (41.8995, -87.9403, None, "suburb"),
    "lombard": (41.8800, -88.0078, None, "suburb"),
    "oak brook": (41.8328, -87.9290, None, "suburb"),
    "oakbrook terrace": (41.8500, -87.9645, None, "suburb"),
    "wheaton": (41.8661, -88.1070, None, "suburb"),
    "warrenville": (41.8178, -88.1734, None, "suburb"),
    "winfield": (41.8617, -88.1609, None, "suburb"),
    "schaumburg": (42.0334, -88.0834, None, "suburb"),
    "hoffman estates": (42.0629, -88.1227, None, "suburb"),
    "rosemont": (41.9953, -87.8845, 60, "suburb"),
    "des plaines": (42.0334, -87.8834, None, "suburb"),
    "park ridge": (42.0111, -87.8406, None, "suburb"),
    "niles": (42.0189, -87.8028, None, "suburb"),
    "morton grove": (42.0406, -87.7826, None, "suburb"),
    "lincolnwood": (42.0045, -87.7300, None, "suburb"),
    "glenview": (42.0698, -87.7878, None, "suburb"),
    "wilmette": (42.0723, -87.7228, None, "suburb"),
    "northbrook": (42.1275, -87.8290, None, "suburb"),
    "northfield": (42.0997, -87.7809, None, "suburb"),
    "deerfield": (42.1711, -87.8445, None, "suburb"),
    "riverwoods": (42.1675, -87.8970, None, "suburb"),
    "bannockburn": (42.1934, -87.8673, None, "suburb"),
    "lincolnshire": (42.1900, -87.9084, None, "suburb"),
    "highland park": (42.1817, -87.8003, None, "suburb"),
    "lake forest": (42.2586, -87.8406, None, "suburb"),
    "mettawa": (42.2453, -87.9212, None, "suburb"),
    "vernon hills": (42.2342, -87.9612, None, "suburb"),
    "libertyville": (42.2831, -87.9531, None, "suburb"),
    "mundelein": (42.2631, -88.0040, None, "suburb"),
    "north chicago": (42.3256, -87.8412, None, "suburb"),
    "abbott park": (42.3020, -87.8920, None, "suburb"),
    "waukegan": (42.3636, -87.8448, None, "suburb"),
    "gurnee": (42.3703, -87.9020, None, "suburb"),
    "arlington heights": (42.0884, -87.9806, None, "suburb"),
    "rolling meadows": (42.0842, -88.0131, None, "suburb"),
    "palatine": (42.1103, -88.0342, None, "suburb"),
    "barrington": (42.1539, -88.1362, None, "suburb"),
    "itasca": (41.9750, -88.0073, None, "suburb"),
    "elk grove village": (42.0039, -87.9703, None, "suburb"),
    "franklin park": (41.9353, -87.8656, None, "suburb"),
    "northlake": (41.9172, -87.8956, None, "suburb"),
    "hillside": (41.8778, -87.9029, None, "suburb"),
    "westchester": (41.8500, -87.8820, None, "suburb"),
    "broadview": (41.8639, -87.8534, None, "suburb"),
    "bolingbrook": (41.6986, -88.0684, None, "suburb"),
    "darien": (41.7520, -87.9739, None, "suburb"),
    "burr ridge": (41.7489, -87.9184, None, "suburb"),
    "willowbrook": (41.7698, -87.9359, None, "suburb"),
    "orland park": (41.6303, -87.8539, None, "suburb"),
    "tinley park": (41.5731, -87.7845, None, "suburb"),
    "blue island": (41.6573, -87.6800, None, "suburb"),
    "harvey": (41.6100, -87.6467, None, "suburb"),
    "calumet city": (41.6156, -87.5295, None, "suburb"),
    "new lenox": (41.5120, -87.9656, None, "suburb"),
    "joliet": (41.5250, -88.0817, None, "suburb"),
    "plainfield": (41.6270, -88.2037, None, "suburb"),
    "oswego": (41.6828, -88.3515, None, "suburb"),
    "geneva": (41.8875, -88.3054, None, "suburb"),
    "st. charles": (41.9142, -88.3087, None, "suburb"),
    "elgin": (42.0354, -88.2826, None, "suburb"),
    "lake zurich": (42.1969, -88.0934, None, "suburb"),
    "lake in the hills": (42.1817, -88.3301, None, "suburb"),
    "hammond": (41.5834, -87.5000, None, "suburb"),
    "gary": (41.5934, -87.3464, None, "suburb"),
}

# Chicago ZIP code centroids (approximate).
ZIPS = {
    "60601": (41.8858, -87.6181), "60602": (41.8829, -87.6321), "60603": (41.8800, -87.6300),
    "60604": (41.8778, -87.6293), "60605": (41.8670, -87.6170), "60606": (41.8823, -87.6378),
    "60607": (41.8721, -87.6578), "60608": (41.8515, -87.6694), "60609": (41.8097, -87.6533),
    "60610": (41.9033, -87.6336), "60611": (41.8955, -87.6175), "60612": (41.8805, -87.6873),
    "60613": (41.9543, -87.6575), "60614": (41.9229, -87.6483), "60615": (41.8022, -87.6006),
    "60616": (41.8445, -87.6260), "60617": (41.7257, -87.5560), "60618": (41.9464, -87.7042),
    "60619": (41.7458, -87.6054), "60620": (41.7412, -87.6543), "60621": (41.7764, -87.6397),
    "60622": (41.9019, -87.6772), "60623": (41.8490, -87.7157), "60624": (41.8804, -87.7223),
    "60625": (41.9703, -87.7042), "60626": (42.0092, -87.6684), "60628": (41.6934, -87.6243),
    "60629": (41.7781, -87.7069), "60630": (41.9699, -87.7603), "60631": (41.9951, -87.8082),
    "60632": (41.8093, -87.7052), "60633": (41.6642, -87.5612), "60634": (41.9463, -87.8061),
    "60636": (41.7760, -87.6674), "60637": (41.7814, -87.6051), "60638": (41.7814, -87.7705),
    "60639": (41.9205, -87.7535), "60640": (41.9719, -87.6624), "60641": (41.9453, -87.7474),
    "60642": (41.9008, -87.6527), "60643": (41.6996, -87.6628), "60644": (41.8829, -87.7578),
    "60645": (42.0086, -87.6947), "60646": (41.9930, -87.7596), "60647": (41.9209, -87.7017),
    "60649": (41.7620, -87.5703), "60651": (41.9025, -87.7392), "60652": (41.7454, -87.7135),
    "60653": (41.8196, -87.6126), "60654": (41.8923, -87.6373), "60655": (41.6948, -87.7038),
    "60656": (41.9743, -87.8271), "60657": (41.9399, -87.6528), "60659": (41.9971, -87.7171),
    "60660": (41.9909, -87.6629), "60661": (41.8814, -87.6430), "60666": (41.9794, -87.9044),
}

CHICAGO_DEFAULT = (41.8837, -87.6289)  # "Chicago, IL" with no site given: most such offices are downtown.
UNLISTED = "Chicago (site not listed)"

NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ontario|united kingdom|\buk\b|england|london|ireland|dublin|germany|berlin|munich|"
    r"france|paris|spain|madrid|valencia|barcelona|italy|milan|netherlands|amsterdam|poland|warsaw|krakow|romania|india|bangalore|"
    r"bengaluru|hyderabad|pune|mumbai|singapore|australia|sydney|melbourne|japan|tokyo|hong kong|china|shanghai|philippines|manila|"
    r"israel|tel aviv|mexico|brazil|sao paulo|argentina|colombia|costa rica|emea|apac|latam|europe)\b"
)
REMOTE = re.compile(r"\b(remote|work from home|wfh|virtual|telecommut)", re.I)
HYBRID = re.compile(r"\bhybrid\b", re.I)
IL_CONTEXT = re.compile(r"\bchicago\b|\billinois\b|\bil\b|\b6[01]\d{3}\b")
# Case-sensitive state codes so English words ("or", "in", "me") don't trigger.
OTHER_STATE = re.compile(
    r"(?:^|[\s,\-/(|])(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
    r"OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WY|DC|IN|WI)(?=$|[\s,\-/)|;])"
    r"|\b(Texas|California|New York|Georgia|Florida|Colorado|Massachusetts|Washington|Arizona|Ohio|Michigan|Minnesota|"
    r"Pennsylvania|New Jersey|North Carolina|Tennessee|Virginia|Maryland|Missouri|Indiana|Wisconsin|Oregon|Utah)\b"
)
# Names that only exist (in practice) in the Chicago area; others need an IL/Chicago hint
# so "Austin, TX", "Aurora, CO" or "Downtown Seattle" are not mistaken for Chicago places.
UNIQUE = {
    "pilsen", "streeterville", "fulton market", "illinois medical district", "bronzeville", "wicker park", "bucktown",
    "logan square", "wrigleyville", "andersonville", "rogers park", "back of the yards", "mckinley park", "little village",
    "river north", "west loop", "south loop", "gold coast", "ukrainian village", "o'hare", "ohare", "evanston", "skokie",
    "schaumburg", "naperville", "oak brook", "des plaines", "northbrook", "glenview", "wilmette", "lincolnshire", "riverwoods",
    "bannockburn", "mettawa", "vernon hills", "libertyville", "mundelein", "waukegan", "gurnee", "abbott park",
    "arlington heights", "rolling meadows", "palatine", "itasca", "elk grove village", "bolingbrook", "burr ridge",
    "willowbrook", "orland park", "tinley park", "calumet city", "new lenox", "joliet", "lake zurich", "lake in the hills",
    "hoffman estates", "downers grove", "western springs", "hinsdale", "elmhurst", "oakbrook terrace", "bedford park",
    "elmwood park", "oak lawn", "north chicago", "morton grove", "lincolnwood", "park ridge", "northlake",
}

_place_names = sorted(PLACES, key=len, reverse=True)
_place_re = re.compile(r"\b(" + "|".join(re.escape(p) for p in _place_names) + r")\b")
_segment_split = re.compile(r";|\||\n| / | or ", re.I)


def miles_between(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def _minutes(miles, kind):
    # Ballpark transit speeds: dense CTA inside the city, slower/transfer-heavy outside.
    if kind == "city":
        return round(8 + 5.5 * miles)
    return round(15 + 4.5 * miles)


def _match_segment(raw_seg):
    """Best (point, name, kind, fixed_minutes, exact) for one location segment, or None."""
    t = raw_seg.lower().replace("–", "-")
    has_il = bool(IL_CONTEXT.search(t))
    if not has_il and OTHER_STATE.search(raw_seg):
        return None
    z = re.search(r"\b(606\d\d)\b", t)
    if z and z.group(1) in ZIPS:
        return ZIPS[z.group(1)], "Chicago " + z.group(1), "city", None, True
    # Take the last place named: in "400 Skokie Blvd, Northbrook, IL" the town follows the street.
    keys = [m.group(1) for m in _place_re.finditer(t) if has_il or m.group(1) in UNIQUE]
    if keys:
        plat, plon, fixed, kind = PLACES[keys[-1]]
        return (plat, plon), keys[-1].title(), kind, fixed, True
    if re.search(r"\bchicago\b", t):
        return CHICAGO_DEFAULT, UNLISTED, "city", None, False
    return None


def locate(text, home, lat=None, lon=None):
    """Return dict(place, miles, minutes, remote, hybrid, in_area, exact, foreign) for a location string.

    Multi-location strings ("Chicago, IL; New York, NY") are split and the closest
    Chicago-area option wins."""
    raw = text or ""
    t = raw.lower()
    home_pt = (home["lat"], home["lon"])
    out = {"place": None, "miles": None, "minutes": None, "remote": bool(REMOTE.search(t)),
           "hybrid": bool(HYBRID.search(t)), "in_area": False, "exact": False, "foreign": bool(NON_US.search(t))}

    candidates = []
    if lat is not None and lon is not None:
        candidates.append(((float(lat), float(lon)), raw.split(",")[0].strip() or "Map pin", None, None, True))
    for seg in _segment_split.split(raw):
        hit = _match_segment(seg)
        if hit:
            candidates.append(hit)

    best = None
    for pt, name, kind, fixed, exact in candidates:
        miles = miles_between(home_pt, pt)
        if miles > 60:
            continue
        kind = kind or ("city" if miles <= 10 else "suburb")
        minutes = fixed if fixed is not None else _minutes(miles, kind)
        if best is None or minutes < best["minutes"]:
            best = {"place": name, "miles": round(miles, 1), "minutes": minutes, "exact": exact}
    if best:
        out.update(best, in_area=True)
    elif out["remote"] and not out["foreign"]:
        # Remote job that isn't tied to another country: commute-free.
        out.update(place="Remote", minutes=0, in_area=True)
    return out
