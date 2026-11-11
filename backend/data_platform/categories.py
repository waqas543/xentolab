"""The canonical category list every source's categories are mapped onto (Gold).

Sources describe businesses in their own vocabularies: OSM tags (`shop=clothes`), Overture's taxonomy
(`clothing_store`), Google's labels ("Clothing store"), GeoNames feature codes. Together that is ~4,000 labels,
many meaning the same thing ("School", "Education", "Educational institution", "High school") and some filed
under several groups at once. `canonical_category` maps any of them, plus the business name when the label says
little, onto one short list of categories, each belonging to exactly one group. The source's own label is kept as
the subcategory.

Rules are keyword patterns over the label, most specific first: "fast food restaurant" is fast food before it is a
restaurant, "dental clinic" a dentist before a clinic, "car repair" automotive before it is a repair. Keyword
syntax (shared with the Google taxonomy): a keyword matches at the start of a word ("pharma" -> "Pharmacy"); a
trailing space makes it a whole word ("bar " is not "Barber"); a leading "~" matches anywhere ("~ologist").

Bump CATEGORY_VERSION whenever a rule changes: Gold records the version it was built with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

CATEGORY_VERSION = "cat-v1"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    group: str
    generic: bool = False      # says little: the business name may refine it


def _rules() -> list[tuple[Category, tuple[str, ...]]]:
    F, R, H, E, FI, A, HO, P, I, PU, O = ("Food & Drink", "Retail", "Health", "Education", "Finance", "Automotive",
                                          "Hospitality", "Professional Services", "Industry & Craft",
                                          "Public & Non-profit", "Other Services")
    c = Category
    return [
        # ---- specific cross-group cases first (their words would match a broader rule later)
        (c("college_university", "College & university", E), ("medical school", "medical college", "dental college",
                                                               "nursing college", "nursing school", "medical university",
                                                               "institute of technology", "medical_school")),
        (c("veterinary", "Veterinary clinic", H), ("veterinar", "animal hospital", "pet hospital", "animal clinic")),
        (c("event_venue", "Wedding & event venue", O), ("wedding hall", "exhibition", "trade cent", "expo cent", "wedding venue", "marriage hall", "banquet",
                                                         "marquee", "event venue", "events venue", "event hall",
                                                         "party hall", "convention cent", "conference cent",
                                                         "function hall", "wedding service", "lawn ")),
        (c("event_planning", "Event planning", P), ("party and event planning", "marriage celebrant", "event planner", "event management",
                                                     "wedding planner", "event planning", "party planner",
                                                     "party equipment", "event technology", "tent rental")),
        (c("religious_school", "Madrasa / religious school", E), ("madrasa", "madrassa", "madrasah", "seminary",
                                                                  "islamic school", "religious school", "quran",
                                                                  "hifz", "darul uloom", "jamia ")),
        (c("driving_school", "Driving school", E), ("driving school", "driving training")),
        (c("language_school", "Language school", E), ("language school", "english language", "language institute",
                                                      "language center", "language centre", "ielts")),
        (c("beauty_school", "Beauty & fashion school", E), ("beauty school", "fashion design school",
                                                            "cosmetology school")),
        (c("education_consultant", "Education consultant", E), ("educational consultant", "immigration", "education consultant",
                                                                "study abroad", "student visa", "immigration consult",
                                                                "visa consult")),
        (c("repair_services", "Repair services", O), ("phone repair", "mobile repair", "laptop repair",
                                                      "computer repair", "appliance repair", "electronics repair",
                                                      "watch repair", "tv repair", "mobile phone repair")),
        (c("dentist", "Dentist", H), ("dental", "dentist", "orthodont", "periodont", "endodont", "denture",
                                      "oral surg", "teeth")),
        (c("eye_care", "Eye care & optician", H), ("optician", "optical", "optometr", "eye care", "eye hospital",
                                                   "eye clinic", "ophthalm", "eyewear", "eyeglass", "lasik",
                                                   "contact lens", "sunglasses")),
        (c("laboratory", "Laboratory & diagnostics", H), ("laborator", "medical lab", "diagnostic", "pathology",
                                                          "x-ray", "x ray", "radiolog", "imaging", "blood test", "blood and plasma", "plasma donation",
                                                          "blood bank", "collection point", "mri ", "ultrasound",
                                                          "lab ")),
        (c("pharmacy", "Pharmacy", H), ("pharma", "vitamin", "supplement store", "drugstore", "drug store", "chemist", "medical store",
                                        "medicine store", "medicine shop")),
        (c("physiotherapy", "Physiotherapy & rehabilitation", H), ("physio", "physical therap", "rehabilitat",
                                                                   "chiropract", "occupational therap",
                                                                   "speech therap", "osteopath")),
        (c("mental_health", "Mental health", H), ("psychiatr", "psycholog", "mental health", "counsel",
                                                  "addiction", "drug rehab", "de-addiction", "drug treatment", "alcohol")),
        (c("alternative_medicine", "Herbal & alternative medicine", H), ("homeopath", "hakeem", "hakim", "herbal",
                                                                         "ayurved", "unani", "alternative medic",
                                                                         "acupunct", "hijama", "naturopath",
                                                                         "traditional chinese medicine", "alternative ")),
        (c("medical_supplies", "Medical supplies", H), ("medical supply", "medical equipment", "surgical",
                                                        "hospital equipment", "orthopedic goods", "wheelchair",
                                                        "hearing aid", "medical_supply")),
        (c("care_home", "Care home", H), ("nursing home", "elder", "old age home", "retirement home", "hospice",
                                          "assisted living", "aged care", "home health")),
        (c("hospital", "Hospital", H), ("hospital", "cancer treatment", "cancer cent", "medical college hospital", "trauma cent", "maternity home",
                                        "emergency room")),
        (c("doctor", "Doctor & specialist", H), ("doctor", "physician", "~surgeon", "nutritionist", "dietitian", "dietician", "~ologist", "pediatric",
                                                  "paediatric", "gynec", "gynaec", "obstetric", "dermatolog",
                                                  "general practitioner", "specialist", "surgery", "cardiolog", "urolog", "neurolog", "nephrolog", "gastro", "orthoped", "oncolog", "pulmonolog", "rheumatolog", "endocrin", "nutrition", "ent specialist", "family practice", "consultant physician",
                                                  "medical practitioner", "fertility", "sexolog", "child health",
                                                  "skin care clinic", "hair transplant", "cosmetic surg",
                                                  "plastic surg")),
        (c("clinic", "Clinic & medical centre", H), ("clinic", "medical cent", "medical centre", "health cent",
                                                     "dispensary", "polyclinic", "basic health unit",
                                                     "health unit", "maternity", "dialysis", "diabetes", "kidney cent", "heart cent", "family planning",
                                                     "outpatient", "urgent care", "surgery cent", "community health",
                                                     "medical group", "family practice")),
        (c("other_health", "Health service", H, True), ("ambulance", "medical service", "health care", "health_care", "healthcare", "medical",
                                                        "health", "unspecified healthcare")),

        # ---- automotive
        (c("fuel_station", "Fuel station", A), ("fuel", "gas station", "petrol", "filling station", "cng",
                                                 "lpg", "service station", "truck stop", "ev charging",
                                                 "charging station", "gas_station")),
        (c("car_wash", "Car wash", A), ("car wash", "car_wash", "auto detailing", "car detailing", "bike wash",
                                        "detailing")),
        (c("car_rental", "Car rental", A), ("car rental", "car_rental", "rent a car", "rent-a-car", "vehicle rental",
                                            "motorcycle rental", "van rental", "truck rental")),
        (c("auto_parts", "Auto parts", A), ("auto parts", "car parts", "auto_parts", "car_parts", "spare parts",
                                            "car accessor", "auto accessor", "motorcycle parts", "tyre", "tire",
                                            "battery store", "auto spare", "car stereo", "car audio")),
        (c("auto_repair", "Car & motorcycle repair", A), ("car repair", "car_repair", "auto repair", "mechanic",
                                                          "auto workshop", "body shop", "auto body",
                                                          "motorcycle repair", "motorcycle_repair", "oil change",
                                                          "transmission", "engine", "auto electric",
                                                          "car service", "vehicle repair", "auto painting",
                                                          "denting", "car glass", "radiator", "vehicle inspection",
                                                          "smog", "wheel alignment", "tuning")),
        (c("motorcycle_dealer", "Motorcycle dealer", A), ("motorcycle", "motor scooter", "scooter", "motorbike",
                                                          "bike dealer", "yamaha", "harley", "rickshaw")),
        (c("car_dealer", "Car dealer", A), ("car dealer", "auto dealer", "car_dealer", "auto_dealer", "used car",
                                            "car showroom", "truck dealer", "vehicle dealer", "dealer of",
                                            "toyota", "suzuki", "honda", "kia ", "hyundai", "audi ", "bmw ",
                                            "mercedes", "nissan", "changan", "mg dealer", "land rover", "car ",
                                            "automobile", "motor vehicle")),
        (c("other_automotive", "Automotive service", A, True), ("automotive", "auto ", "vehicle", "vehicle_service",
                                                                "towing", "parking")),

        # ---- food & drink
        (c("fast_food", "Fast food", F), ("fast food", "fast_food", "burger", "~burger", "pizza", "shawarma",
                                           "sandwich", "fried chicken", "chicken shop", "hot dog", "takeout",
                                           "take away", "food court", "food stand", "food truck", "kiosk",
                                           "fish and chips", "snack", "broast", "chaat", "samosa", "pakora",
                                           "paratha roll", "roll shop", "falafel", "kebab", "food_court")),
        (c("ice_cream", "Ice cream & desserts", F), ("ice cream", "ice_cream", "frozen yogurt", "gelato",
                                                      "dessert", "kulfi", "falooda", "waffle", "cupcake")),
        (c("sweets_shop", "Sweets & mithai", F), ("sweet", "mithai", "confection", "candy", "chocolate")),
        (c("bakery", "Bakery", F), ("baker", "bakery", "cake", "pastry", "patisserie", "bread", "cookie",
                                    "donut", "doughnut")),
        (c("juice_bar", "Juice bar", F), ("juice", "smoothie", "shake ", "sugarcane", "lassi")),
        (c("cafe", "Cafe & tea", F), ("cafe", "café", "coffee", "tea house", "tea_house", "tea room", "chai",
                                      "tea shop", "bubble tea", "teahouse", "bistro")),
        (c("catering", "Catering", F), ("cater", "tiffin", "meal delivery", "food delivery", "home food",
                                        "lunch box")),
        (c("bar", "Bar & pub", F), ("bar ", "beer garden", "pub ", "biergarten", "lounge", "wine bar", "cocktail", "beer ",
                                    "brewpub", "hookah", "shisha", "nightlife")),
        (c("restaurant", "Restaurant", F), ("restaurant", "dhaba", "eatery", "grill", "barbecue", "bbq",
                                            "steak", "buffet", "diner", "karahi", "biryani", "nihari", "haleem",
                                            "tikka", "seafood", "sushi", "dining", "food and drink",
                                            "food_and_drink", "chophouse", "hotel and restaurant", "noodle",
                                            "chinese", "cuisine")),

        # ---- lodging (Pakistani "hotels" are often eateries, so names alone never decide this)
        (c("guest_house", "Guest house", HO), ("guest house", "guest_house", "guesthouse", "bed and breakfast",
                                                "homestay", "home stay", "inn ", "bed & breakfast", "farmstay",
                                                "rest house")),
        (c("hostel", "Hostel", HO), ("hostel", "boarding house", "dormitor", "student accommodation",
                                     "student housing")),
        (c("resort", "Resort", HO), ("resort", "chalet", "camp site", "campground", "camping", "glamping",
                                     "holiday park", "cottage")),
        (c("serviced_apartment", "Serviced apartment", HO), ("serviced apartment", "service apartment", "apartment", "holiday apartment", "serviced accommodation",
                                                              "furnished apartment", "holiday apartment",
                                                              "vacation rental", "holiday home", "apartment hotel",
                                                              "short term apartment", "aparthotel", "villa ")),
        (c("hotel", "Hotel", HO), ("hotel", "motel", "lodging", "lodge", "accommodation")),

        # ---- finance
        (c("money_exchange", "Money exchange & transfer", FI), ("currency exchange", "money exchange",
                                                                 "exchange company", "money transfer",
                                                                 "bureau de change", "bureau_de_change",
                                                                 "money_transfer", "remittance", "mobile money",
                                                                 "western union", "payment cent", "payment_centre",
                                                                 "easypaisa", "jazzcash", "check cashing",
                                                                 "foreign exchange", "forex")),
        (c("insurance", "Insurance", FI), ("insurance", "loss adjuster", "takaful")),
        (c("accounting_tax", "Accounting & tax", FI), ("accountant", "accounting", "audit", "bookkeep", "tax ",
                                                        "taxation", "tax_advisor", "chartered account",
                                                        "income tax", "tax consult", "tax preparation",
                                                        "payroll")),
        (c("investment", "Investment & brokerage", FI), ("invest", "stock broker", "stockbroker", "brokerage",
                                                          "securities", "asset management", "wealth",
                                                          "financial advis", "financial_advisor", "mutual fund",
                                                          "leasing", "modaraba", "stock exchange", "trading platform",
                                                          "venture capital", "private equity", "commodit")),
        (c("bank", "Bank", FI), ("bank", "atm ", "credit union", "microfinance", "savings", "loan",
                                 "mortgage", "lender")),
        (c("other_finance", "Financial service", FI, True), ("financ", "financial", "money")),

        # ---- education
        (c("preschool", "Preschool & daycare", E), ("preschool", "pre school", "pre-school", "kindergarten",
                                                    "montessori", "nursery school", "playgroup", "daycare",
                                                    "day care", "childcare", "child care", "creche")),
        (c("college_university", "College & university", E), ("college", "universit", "polytechnic",
                                                               "faculty of", "academic department", "campus",
                                                               "medical school", "law school", "business school",
                                                               "graduate school", "postgraduate")),
        (c("academy_tuition", "Academy & tuition", E), ("academy", "tuition", "tutor", "coaching", "test prep",
                                                        "learning cent", "learning center", "studying cent",
                                                        "exam prep", "home tutor", "quran teacher")),
        (c("vocational_training", "Vocational & technical training", E), ("training", "vocational", "technical",
                                                                          "skills", "computer institute",
                                                                          "computer training", "it institute",
                                                                          "trade school", "cooking school",
                                                                          "art school", "music school",
                                                                          "dance school", "flight school",
                                                                          "nursing school", "beautician course",
                                                                          "music_school")),
        (c("school", "School", E), ("school", "school house", "primary", "elementary", "secondary", "middle school",
                                    "high school", "grammar", "matric", "gbps", "ggps", "gbhs", "gghs", "gbms",
                                    "ggms", "gblss", "gglss")),
        (c("other_education", "Education", E, True), ("education", "educational", "institute", "tutoring",
                                                      "teacher", "library_and_education")),

        # ---- retail (specific shops before markets and generic shops)
        (c("mobile_phones", "Mobile phones", R), ("mobile phone", "mobile_phone", "cell phone", "cellphone",
                                                  "smartphone", "phone store", "phone shop", "mobile shop",
                                                  "mobile accessor", "telephone store")),
        (c("computers", "Computers & IT equipment", R), ("computers ", "computer store", "computer shop", "laptop",
                                                          "computer accessor", "computer hardware",
                                                          "computer_store", "it equipment", "printer store",
                                                          "computer ")),
        (c("electronics_appliances", "Electronics & appliances", R), ("electronic", "appliance", "home appliance",
                                                                      "television", "tv store", "camera store",
                                                                      "audio", "video game", "hifi", "air conditioner",
                                                                      "ups ", "solar panel", "generator",
                                                                      "lighting store", "electrical supply", "electrical ", "camera and photo",
                                                                      "electrical store", "electrical goods",
                                                                      "electrical_supply", "lamp")),
        (c("grocery", "Grocery & supermarket", R), ("grocery", "groceri", "supermarket", "hypermarket",
                                                    "convenience", "general store", "kiryana", "karyana",
                                                    "variety store", "variety_store", "department store",
                                                    "department_store", "cash and carry", "warehouse club", "discount store", "general ", "mart ", "superstore",
                                                    "provision", "food store", "food_and_beverage_store",
                                                    "dry fruit", "spice", "rice shop", "organic store",
                                                    "health food", "halal")),
        (c("meat_poultry", "Butcher & poultry", R), ("butcher", "meat", "poultry shop", "chicken store",
                                                     "fish store", "fish market", "seafood market", "fishmonger")),
        (c("fruit_vegetables", "Fruit & vegetables", R), ("greengrocer", "produce", "fruit", "vegetable", "produce market",
                                                          "sabzi")),
        (c("dairy_shop", "Dairy shop", R), ("dairy", "milk shop", "milk_shop", "yogurt shop", "dahi")),
        (c("fabric_textiles", "Fabric & textiles", R), ("fabric", "cloth store", "cloth shop", "textile store",
                                                        "textile shop", "unstitched", "lawn store", "bedding",
                                                        "curtain", "linen", "upholstery shop", "sewing",
                                                        "haberdash", "yarn")),
        (c("shoes", "Shoes", R), ("shoe", "footwear", "boot store", "sneaker", "chappal")),
        (c("jewelry", "Jewellery", R), ("jewel", "jewellery", "jewelry", "gold ", "goldsmith", "silver ",
                                        "diamond", "bangles", "watch store", "watch shop", "watches")),
        (c("cosmetics", "Cosmetics & perfume", R), ("cosmetic", "hair supply", "health and beauty", "perfume", "fragrance", "beauty supply",
                                                    "beauty product", "skin care product", "attar", "makeup store",
                                                    "beauty_supply")),
        (c("clothing", "Clothing", R), ("cloth", "clothing", "clothes", "fashion", "boutique", "garment",
                                        "apparel", "tailor-made", "sportswear", "bridal", "wedding dress",
                                        "lingerie", "uniform", "kids wear", "menswear", "womenswear",
                                        "abaya", "hijab", "shawl", "wear ")),
        (c("furniture", "Furniture", R), ("furniture", "mattress", "bed ", "sofa", "bed store", "office furniture",
                                          "kitchen cabinet", "home decor", "interior decor", "carpet", "rug",
                                          "flooring store")),
        (c("hardware_building", "Hardware & building materials", R), ("hardware", "building material", "paint",
                                                                      "sanitary", "tile", "plumbing supply",
                                                                      "marble", "glass", "timber", "lumber",
                                                                      "cement", "steel", "iron", "pipe",
                                                                      "doityourself", "doit", "diy", "tool", "home improvement", "building supply", "granite",
                                                                      "plywood", "bathroom supply",
                                                                      "kitchen supply", "door", "window",
                                                                      "aluminium", "aluminum", "electric fitting",
                                                                      "fasteners", "trade_and_repair")),
        (c("home_goods", "Home & kitchen goods", R), ("home goods", "houseware", "household", "kitchenware",
                                                      "crockery", "home store", "home_goods", "homeware",
                                                      "cookware", "plastic", "gift", "souvenir", "decoration",
                                                      "party store", "party supply", "handicraft", "craft store",
                                                      "art supply", "antique", "frame", "candle", "florist",
                                                      "flower", "garden center", "garden centre", "plant nursery",
                                                      "nursery")),
        (c("books_stationery", "Books & stationery", R), ("book", "stationer", "office supply", "newsagent",
                                                          "magazine", "newspaper stand", "comic", "school supply",
                                                          "copy", "photocopy")),
        (c("sports_toys", "Sports, toys & hobbies", R), ("sport", "toy", "game store", "bicycle", "cycle", "bike store", "luggage",
                                                         "fitness equipment", "outdoor", "fishing", "hunting",
                                                         "arms", "gun ", "baby store", "baby_store", "kids store",
                                                         "music store", "musical instrument", "hobby")),
        (c("pet_store", "Pet store", R), ("pet store", "pet shop", "pet supply", "aquarium", "pet_store",
                                          "bird shop", "pets ")),
        (c("tobacco", "Tobacco & vape", R), ("tobacco", "vape", "e-cigarette", "cigar", "paan", "pan shop")),
        (c("shopping_mall", "Shopping mall & plaza", R), ("shopping mall", "shopping_mall", "mall", "plaza",
                                                          "shopping cent", "shopping complex", "outlet mall",
                                                          "arcade ")),
        (c("market", "Market & bazaar", R), ("market", "bazar", "bazaar", "marketplace", "flea market",
                                             "farmers market", "mandi")),
        (c("wholesale", "Wholesale & distribution", R), ("wholesale", "distributor", "supplier", "trader",
                                                         "trading", "merchant", "importer", "stockist",
                                                         "dealer")),
        (c("other_shop", "Shop", R, True), ("store", "shop", "showroom", "outlet", "retail", "shopping",
                                            "unspecified_shop", "boutique", "emporium", "depot", "kiosk")),

        # ---- industry & craft
        (c("brick_kiln_quarry", "Brick kiln & quarry", I), ("brick", "kiln", "quarr", "stone crush", "crusher",
                                                             "gravel", "sand ", "brickyard")),
        (c("textile_mill", "Textile & garment manufacturing", I), ("textile", "spinning", "weaving", "knitting",
                                                                   "dyeing", "garment factory", "garment manufact",
                                                                   "hosiery", "denim", "towel", "ginning",
                                                                   "embroider", "stitching unit", "loom")),
        (c("food_processing", "Food processing", I), ("flour mill", "slaughter", "abattoir", "bottled water", "mineral water", "water company", "water plant", "rice mill", "sugar mill", "oil mill",
                                                      "food processing", "food manufact", "food producer",
                                                      "dairy farm", "dairy plant", "cold storage", "ice factory",
                                                      "bottling", "beverage manufact", "brewery", "distillery",
                                                      "chakki", "mill")),
        (c("agriculture", "Agriculture & farming", I), ("farm", "ranch", "greenhouse", "grain", "animal breeding", "breeder", "agricultur", "livestock", "poultry", "fish farm",
                                                        "fishery", "orchard", "seed", "fertili", "pesticide",
                                                        "tractor", "agro", "cattle", "goat", "horticult",
                                                        "irrigation", "agricultural_service", "veterinary supply",
                                                        "feed ")),
        (c("energy_utilities", "Energy & utilities", I), ("power station", "water treatment", "communications tower", "oilfield", "oil field", "substation", "electrical substation", "power plant", "electric utility",
                                                          "solar energy", "solar", "oil and gas", "oil & gas",
                                                          "petroleum", "refinery", "gas company", "natural gas",
                                                          "energy", "utility", "water supply", "electricity",
                                                          "hydro", "wind farm", "oil refinery", "public_utility",
                                                          "grid station", "mining", "mine ")),
        (c("construction", "Construction & contractors", I), ("construction", "contractor", "builder",
                                                              "civil engineer", "building or construction",
                                                              "building_or_construction", "home developer",
                                                              "home_developer", "property developer",
                                                              "real estate developer", "renovation", "roofing",
                                                              "demolition", "excavat", "paving", "waterproof",
                                                              "scaffold", "concrete", "structural")),
        (c("metal_engineering", "Metal & engineering works", I), ("metal", "iron works", "ironworks", "steel fab",
                                                                  "fabricat", "welding", "welder", "foundry",
                                                                  "machine shop", "machin", "engineering works",
                                                                  "lathe", "cnc", "blacksmith", "workshop",
                                                                  "tool and die", "spring", "casting",
                                                                  "metal_construction")),
        (c("trades", "Electrician, plumber & trades", I), ("plumbing", "electrician", "woodwork", "electrical install", "elevator", "lift ", "plumber", "carpenter", "joiner",
                                                           "painter", "mason", "hvac", "air condition",
                                                           "ac repair", "ac service", "refrigerat", "glazier",
                                                           "locksmith", "handyman", "home service",
                                                           "home_service", "pest control", "interior fit",
                                                           "pool cleaning", "solar install", "gardener",
                                                           "landscap", "tiling", "plastering")),
        (c("packaging_printing", "Packaging & print production", I), ("packaging", "printing press",
                                                                      "offset print", "label manufact", "carton",
                                                                      "corrugat", "flexible packaging")),
        (c("import_export", "Import & export", I), ("exporter", "export", "import", "import and export",
                                                    "international trade", "freight forward")),
        (c("manufacturing", "Manufacturing", I), ("manufactur", "factor", "industr", "plant ",
                                                  "works", "assembly", "processing", "chemical",
                                                  "pharmaceutical", "plastic", "rubber", "pvc",
                                                  "furniture manufact", "paper", "glass manufact",
                                                  "pharmaceutical company", "surgical instrument",
                                                  "sports goods manufact", "leather", "tannery", "factory_outlet")),
        (c("other_industry", "Industry", I, True), ("industrial", "recycl", "waste", "scrap", "craft", "warehouse", "storage", "wholesaler")),

        # ---- professional services
        (c("real_estate", "Real estate", P), ("real estate", "condominium rental", "rental agency", "real_estate", "estate agent", "estate_agent",
                                              "property", "properties", "realtor", "housing scheme",
                                              "land development", "property management", "letting")),
        (c("legal", "Legal services", P), ("law", "lawyer", "attorney", "advocate", "legal", "notary",
                                           "barrister", "solicitor", "court ", "paralegal", "attorney_or_law_firm")),
        (c("it_software", "IT & software", P), ("software", "it service", "it_service", "it company",
                                                "web design", "web develop", "website design", "app develop",
                                                "mobile app", "computer service", "computer support",
                                                "information technology", "information services", "data ",
                                                "cloud", "cyber", "hosting", "digital solution", "tech company",
                                                "technology", "it ", "it_", "seo", "software_development",
                                                "bpo", "call cent", "e-commerce", "ecommerce")),
        (c("telecom", "Telecom & internet", P), ("telecom", "telephone exchange", "internet service", "internet provider", "isp ",
                                                 "network operator", "mobile network", "cable tv", "cable company",
                                                 "satellite", "broadband", "telephone company", "fiber",
                                                 "telecommunication", "franchise of jazz", "mobilink", "zong",
                                                 "ufone", "telenor", "ptcl", "jazz ")),
        (c("marketing_media", "Marketing, media & advertising", P), ("marketing", "telemarketing", "media house", "production", "advertis", "digital agency",
                                                                     "media", "public relations", "pr agency",
                                                                     "branding", "creative agency", "social media",
                                                                     "broadcast", "radio", "television station",
                                                                     "tv station", "newspaper", "publisher",
                                                                     "publishing", "film", "video production",
                                                                     "production house", "studio", "news",
                                                                     "media_service", "content")),
        (c("printing_signage", "Printing & signage", P), ("print", "printer", "sign", "banner", "screen printing",
                                                          "copyshop", "copy shop", "digitizing", "stamp", "flex ")),
        (c("design_architecture", "Design & architecture", P), ("architect", "interior design", "interior_design",
                                                                "graphic design", "design", "surveyor", "planning",
                                                                "urban planning", "landscape architect")),
        (c("engineering_consulting", "Engineering & technical consulting", P), ("engineer", "automation", "biotech", "research", "laboratory equipment", "research and", "research cent", "research institute", "r and d", "engineering",
                                                                                "testing", "inspection",
                                                                                "geotechnical", "environmental",
                                                                                "laboratory testing")),
        (c("travel_agency", "Travel agency & tours", P), ("travel", "ticket", "tour", "tourism", "visa", "hajj", "umrah",
                                                          "ticketing", "air ticket", "airline ticket",
                                                          "travel_service", "travel_agency", "pilgrimage")),
        (c("logistics", "Courier, logistics & transport", P), ("courier", "airport", "airfield", "moving", "railway", "bus ticket", "port ", "logistic", "cargo", "freight",
                                                               "shipping", "delivery", "mover", "packers",
                                                               "transport", "trucking", "haulage", "goods",
                                                               "bus company", "bus service", "coach", "airline",
                                                               "taxi", "ride", "container", "customs",
                                                               "clearing agent", "weighbridge", "postal service",
                                                               "travel_and_transportation")),
        (c("recruitment", "Recruitment & staffing", P), ("employment", "recruit", "manpower", "staffing",
                                                         "overseas employment", "job", "headhunt", "hr ",
                                                         "human resource", "placement")),
        (c("security_services", "Security services", P), ("security", "guard", "cctv", "alarm", "surveillance",
                                                           "fire protection", "fire safety")),
        (c("consulting", "Business consulting", P), ("certification", "consult", "occupational safety", "auction", "appraisal", "billing", "broker", "valuation", "advisory", "management", "business service",
                                                     "business develop", "outsourc", "translation",
                                                     "typing", "notary public", "documentation", "registration")),
        (c("coworking_office", "Company office", P, True), ("corporate office", "corporate_office", "head office",
                                                            "headquarter", "office", "company", "business cent",
                                                            "coworking", "co-working", "corporate", "holding",
                                                            "enterprise", "firm")),
        (c("other_professional", "Professional service", P, True), ("professional service", "professional_service",
                                                                    "services_and_business", "service", "agency",
                                                                    "business", "b2b")),

        # ---- public & non-profit
        (c("post_office", "Post office", PU), ("post office", "post_office", "postal", "pakistan post")),
        (c("embassy", "Embassy & consulate", PU), ("embassy", "consulate", "diplomatic", "high commission")),
        (c("political_party", "Political party", PU), ("political party", "political_party", "political")),
        (c("religious_organization", "Religious organisation", PU), ("religious organi", "religious_organi",
                                                                     "islamic cent", "islamic organi", "dargah",
                                                                     "imambargah", "religion", "missionary",
                                                                     "church organi", "religious cent")),
        (c("ngo_charity", "NGO & charity", PU), ("ngo", "social worker", "housing program", "environment", "non-profit", "nonprofit", "non profit", "charit",
                                                 "foundation", "trust", "welfare", "social service",
                                                 "community service", "social or community", "social_or_community",
                                                 "volunteer", "orphan", "food bank", "shelter", "relief",
                                                 "humanitarian", "non-governmental", "social_facility")),
        (c("association", "Association & club", PU), ("association", "social centre", "social center", "social club", "village hall", "community hall", "union", "chamber of", "federation",
                                                      "society", "club", "council", "guild", "bar council",
                                                      "press club", "alumni", "cooperative", "organization",
                                                      "organisation")),
        (c("government_office", "Government office", PU), ("government", "administrative", "federal", "ministry", "department", "district office",
                                                           "municipal", "tehsil", "authority", "board", "commission",
                                                           "directorate", "secretariat", "assembly", "nadra",
                                                           "passport", "excise", "registration office",
                                                           "license office", "licence office", "tax office",
                                                           "public works", "public prosecutor", "public defender",
                                                           "court", "union council", "cantonment", "wapda",
                                                           "town committee", "civic", "public service",
                                                           "city hall", "community_and_government",
                                                           "military", "army", "police", "rescue", "1122")),
        (c("other_public", "Public service", PU, True), ("public", "community", "institution", "civil")),

        # ---- other services
        (c("barber", "Barber", O), ("barber", "men's salon", "mens salon", "gents salon", "hair cutting",
                                    "haircut")),
        (c("beauty_salon", "Beauty salon & spa", O), ("beauty", "beautic", "make up", "hair replacement", "salon", "parlour", "parlor", "spa ", "spa_",
                                                       "nail", "makeup", "make-up", "bridal makeup", "hairdress",
                                                       "hair salon", "waxing", "threading", "massage", "facial",
                                                       "tanning", "tattoo", "piercing", "wellness", "sauna",
                                                       "eyebrow", "mehndi", "henna", "body shaping",
                                                       "laser hair")),
        (c("fitness_sports", "Gym, fitness & sports", O), ("gym", "fitness", "yoga", "martial art", "karate",
                                                           "boxing", "sports club", "sports complex", "swimming",
                                                           "cricket", "football", "futsal", "tennis", "squash",
                                                           "golf", "snooker", "billiard", "bowling", "stadium",
                                                           "sports_and_recreation", "pilates", "aerobics",
                                                           "crossfit", "horse riding", "padel", "skating",
                                                           "badminton", "sports centre", "sports center",
                                                           "recreation")),
        (c("tailor", "Tailor", O), ("tailor", "dressmaker", "stitching", "alteration", "darzi", "embroidery shop")),
        (c("laundry", "Laundry & dry cleaning", O), ("laundr", "dry clean", "dry_cleaning", "ironing", "washing")),
        (c("repair_services", "Repair services", O), ("repair", "mobile repair", "phone repair", "computer repair",
                                                      "watch repair", "shoe repair", "electronics repair",
                                                      "appliance repair", "cobbler", "key cutting")),
        (c("photography", "Photography & video", O), ("photograph", "photo booth", "photo ", "photo studio", "photo lab", "videograph",
                                                      "studio photo", "camera")),
        (c("cleaning", "Cleaning services", O), ("cleaning", "cleaners", "janitorial", "maid", "housekeeping", "carpet clean",
                                                 "car cleaning", "sanitiz", "disinfect", "water tank")),
        (c("entertainment", "Entertainment & leisure", O), ("cinema", "arts centre", "arts center", "casino", "go kart", "karting", "paintball", "movie", "theater", "theatre", "amusement",
                                                            "theme park", "water park", "arcade", "gaming",
                                                            "play area", "playland", "escape room", "karaoke",
                                                            "nightclub", "night club", "zoo ", "aquarium ",
                                                            "museum", "art gallery", "gallery", "arts_and_entertainment",
                                                            "internet cafe", "game zone", "trampoline", "carnival",
                                                            "circus", "concert", "music venue", "comedy",
                                                            "entertainment", "leisure")),
        (c("pet_services", "Pet services", O), ("pet groom", "animal or pet", "pet service", "pet care", "pet boarding", "dog train", "pet sitting",
                                                "kennel", "dog center", "dog centre", "animal shelter", "cattery")),
        (c("funeral", "Funeral services", O), ("funeral", "undertaker", "mortuar", "crematori", "burial")),
        (c("childcare_services", "Household & personal services", O), ("babysit", "home help", "house sitter", "professional organizer", "nanny", "domestic", "personal",
                                                                       "home care", "driver service",
                                                                       "marriage bureau", "matrimonial",
                                                                       "rishta", "astrolog", "lifestyle_services",
                                                                       "lifestyle")),
        (c("other_services", "Other services", O, True), ("other services", "services", "local services",
                                                          "local_services")),
    ]


# Labels that are places, not businesses: natural features, worship, public space, infrastructure, housing.
_EXCLUDED = (
    "river", "mountain", "peak", "ravine", "lake ", "island", "valley", "beach", "forest", "waterfall", "glacier",
    "desert", "nature reserve", "national park", "wildlife", "canyon", "hill ", "spring ", "stream", "pond",
    "mosque", "masjid", "church", "temple", "gurdwara", "shrine", "place of worship", "place_of_worship",
    "cemetery", "graveyard", "grave yard", "grave_yard", "park ", "parks", "playground", "public garden",
    "garden ", "bus stop", "bus station", "railway station", "train station", "metro station", "airport terminal",
    "bridge", "road ", "street", "highway", "intersection", "roundabout", "neighbourhood", "neighborhood",
    "village", "hamlet", "locality", "administrative area", "housing society", "housing complex",
    "housing development", "residential", "apartment building", "apartment complex", "condominium", "landmark",
    "monument", "historical", "historic site", "tourist attraction", "viewpoint", "scenic", "public toilet",
    "toilets", "fountain", "library", "religious destination", "toll station", "toll plaza", "smoking area", "hiking", "monastery", "highway patrol", "border crossing", "rest stop", "archive", "geographic_entities", "cultural_and_historic", "parking lot",
    "parking_lot", "parking garage", "dam ",
)
_NOT_EXCLUDED = ("amusement park", "theme park", "water park", "trampoline park", "business park",
                 "industrial park", "technology park", "software park", "garden center", "garden centre",
                 "garden furniture", "village hall", "fish market", "park view", "water park", "spring mattress",
                 "street food", "hill station resort", "valley resort", "mountain resort", "lake resort",
                 "river side", "riverside", "home and garden", "media house", "apartment rental", "serviced apartment", "service apartment", "residential college", "spring supplier", "condominium rental", "rental agency", "apartment hotel", "house sitter", "house cleaning", "boarding house", "guest house", "rest house", "warehouse", "publishing house", "coffee house", "tea house", "fashion house", "software house", "printing house", "auction house", "steak house", "clearing house", "design house", "production house", "trading house", "house of", "safari park", "wildlife park", "beach resort", "island resort")

# Strong signals in a business name, used when the category says little (missing, "Shop", "Company") or when
# the name contradicts a category that is often wrong (a "marriage hall" filed as a nightclub).
_NAME_OVERRIDES = ("event_venue", "brick_kiln_quarry", "veterinary", "religious_school")
_NAME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("event_venue", ("marriage hall", "marriage club", "marquee", "banquet", "wedding hall", "shadi hall",
                     "marriage garden", "party hall", "event complex")),
    ("brick_kiln_quarry", ("brick kiln", "bhatta", "kiln", "crush plant", "stone crusher")),
    ("veterinary", ("veterinary", "vet clinic", "pet clinic", "animal hospital")),
    ("religious_school", ("madrasa", "madrassa", "madrasah", "jamia ", "darul uloom", "dar ul uloom",
                          "darululoom")),
    ("fuel_station", ("petroleum", "filling station", "petrol pump", "petrol station", "cng station", "fuel station",
                      "pso ", "shell ", "total parco", "attock petroleum", "byco", "hascol")),
    ("hospital", ("hospital",)),
    ("dentist", ("dental", "dentist")),
    ("laboratory", ("laboratory", "diagnostic", " lab ", "labs ", "x-ray")),
    ("pharmacy", ("pharmacy", "medical store", "chemist", "pharma ", "medicos", "medicose")),
    ("clinic", ("clinic", "medical center", "medical centre", "health center", "dispensary", "maternity home")),
    ("college_university", ("university", "college", "campus")),
    ("preschool", ("montessori", "kindergarten", "preschool", "day care", "daycare")),
    ("academy_tuition", ("academy", "tuition", "coaching")),
    ("school", ("school", "gbps", "ggps", "gbhs", "gghs", "gbms", "ggms", "gblss", "gglss", "gps ", "ghs ", "gms ")),
    ("bank", ("bank ",)),
    ("money_exchange", ("exchange company", "money exchange", "currency exchange")),
    ("insurance", ("insurance", "takaful")),
    ("bakery", ("bakery", "bakers", "cake")),
    ("sweets_shop", ("sweets", "mithai")),
    ("cafe", ("cafe", "café", "coffee", "chai ")),
    ("fast_food", ("pizza", "burger", "shawarma", "broast", "fried chicken")),
    ("restaurant", ("restaurant", "dhaba", "kitchen", "grill", "karahi", "biryani", "tikka", "nihari")),
    ("guest_house", ("guest house", "guesthouse", "rest house")),
    ("hostel", ("hostel",)),
    ("beauty_salon", ("salon", "parlour", "parlor", "beauty")),
    ("barber", ("barber", "hair cutting")),
    ("fitness_sports", ("gym", "fitness")),
    ("tailor", ("tailor",)),
    ("travel_agency", ("travel", "tours", "hajj", "umrah")),
    ("real_estate", ("real estate", "estate agency", "properties", "property ")),
    ("logistics", ("courier", "logistics", "cargo", "goods transport")),
    ("car_dealer", ("motors", "auto dealers", "car showroom")),
    ("auto_repair", ("auto workshop", "auto works", "auto electric", "car care", "mechanic")),
    ("mobile_phones", ("mobile shop", "mobile center", "mobile centre", "mobile zone", "mobiles")),
    ("hardware_building", ("hardware", "sanitary", "paint ", "tiles", "marble")),
    ("electronics_appliances", ("electronics", "electric store", "electric house")),
    ("grocery", ("general store", "kiryana", "karyana", "super store", "superstore", "mart ", "grocery",
                 "cash & carry", "cash and carry")),
    ("clothing", ("cloth house", "garments", "boutique", "collection")),
    ("jewelry", ("jewellers", "jewelers", "jewellery", "jewelry")),
    ("furniture", ("furniture",)),
    ("legal", ("law associates", "law firm", "advocate", "law chamber", "legal")),
    ("accounting_tax", ("chartered accountants", "& co chartered", "tax consultant")),
    ("it_software", ("software", "technologies", "solutions", "tech ", "digital")),
    ("construction", ("construction", "builders", "developers", "contractor")),
    ("textile_mill", ("textile", "textiles", "spinning", "weaving")),
    ("manufacturing", ("industries", "industry", "manufacturing", "factory", "mills")),
    ("ngo_charity", ("foundation", "welfare", "trust ")),
    ("government_office", ("govt ", "government", "district office", "municipal", "tehsil office")),
    ("post_office", ("post office",)),
    ("trades", ("electrician", "plumber", "carpenter")),
    ("printing_signage", ("printers", "printing", "press ")),
    ("wholesale", ("traders", "trading", "distributors", "enterprises", "suppliers")),
]
# A name that is a mosque or a graveyard is not a business, whatever a provider filed it under.
_NAME_EXCLUDED = ("masjid", "mosque", "jamia masjid", "graveyard", "qabristan", "imambargah", "church",
                  "mandir", "gurdwara", "darbar")
_NAME_EXCLUDED_UNLESS = ("school", "academy", "college", "madrasa", "madrassa", "road", "chowk", "market",
                         "store", "shop", "pharmacy", "medical", "hospital", "clinic", "bakery", "hotel",
                         "restaurant", "trust", "foundation", "welfare")


def _keyword(keyword: str) -> str:
    """A keyword normalized like the text it is matched against (hyphens, underscores, '&')."""
    text = keyword.lower().replace("_", " ").replace("-", " ").replace("&", " and ")
    text = re.sub(r"[^a-z0-9~ ]+", " ", text)
    return re.sub(r" +", " ", text).lstrip(" ") if not text.endswith(" ") else re.sub(r" +", " ", text).strip() + " "


def _pattern(keywords: tuple[str, ...]) -> re.Pattern:
    keywords = tuple(_keyword(k) for k in keywords)
    parts = [re.escape(k[1:]) if k.startswith("~") else r"(?<![a-z0-9])" + re.escape(k) for k in keywords]
    return re.compile("|".join(parts))


# Service businesses whose words would otherwise be read as industry ("Industrial real estate agency",
# "Industrial consultant", "Cable TV company") are tried before the industry rules.
_EARLY = ("real_estate", "legal", "it_software", "telecom", "marketing_media", "travel_agency", "recruitment",
          "security_services", "consulting", "fitness_sports")
_EXACT_EXCLUDED = {"house", "home", "apartment building", "residence", "residential", "building", "place"}


def _ordered(rules: list[tuple[Category, tuple[str, ...]]]) -> list[tuple[Category, tuple[str, ...]]]:
    """Specific rules in their written order (the early service rules moved before industry), then generic ones."""
    early = [r for r in rules if r[0].key in _EARLY]
    rest = [r for r in rules if r[0].key not in _EARLY]
    first_industry = next(i for i, r in enumerate(rest) if r[0].group == "Industry & Craft")
    ordered = rest[:first_industry] + early + rest[first_industry:]
    return [r for r in ordered if not r[0].generic] + [r for r in ordered if r[0].generic]


RULES = [(category, _pattern(keywords)) for category, keywords in _ordered(_rules())]
CATEGORIES: dict[str, Category] = {}
for _category, _ in RULES:
    CATEGORIES.setdefault(_category.key, _category)
GROUP_FALLBACK = {
    "Food & Drink": "restaurant", "Retail": "other_shop", "Health": "other_health", "Education": "other_education",
    "Finance": "other_finance", "Automotive": "other_automotive", "Hospitality": "hotel",
    "Professional Services": "other_professional", "Industry & Craft": "other_industry",
    "Public & Non-profit": "other_public", "Other Services": "other_services",
}
_EXCLUDED_RE = _pattern(_EXCLUDED)
_NOT_EXCLUDED_RE = _pattern(_NOT_EXCLUDED)
_NAME_RULES_RE = [(CATEGORIES[key], _pattern(keywords)) for key, keywords in _NAME_RULES]
_NAME_EXCLUDED_RE = _pattern(_NAME_EXCLUDED)
_NAME_EXCLUDED_UNLESS_RE = _pattern(_NAME_EXCLUDED_UNLESS)
GENERIC_NAME_WORDS = frozenset(
    {re.sub(r"\s+", " ", c.label.lower()) for c in CATEGORIES.values()}
    | {"brick kiln", "school", "shop", "store", "hotel", "restaurant", "bank", "atm", "pharmacy", "clinic", "hospital",
       "bakery", "tailor", "masjid", "mosque", "petrol pump", "filling station", "general store", "medical store",
       "post office", "police station", "office", "company", "factory", "workshop", "market", "timber factory",
       "flour mill", "rice mill", "poultry farm", "dairy farm", "farm", "college", "academy", "salon",
       "beauty parlour", "barber shop", "hair salon", "mobile shop", "karyana store", "kiryana store", "cafe",
       "tea stall", "hotel and restaurant", "car wash", "auto workshop", "dhaba", "marriage hall", "mart",
       "government school", "govt school", "primary school", "high school", "middle school", "boys school",
       "girls school", "government primary school", "government high school", "basic health unit",
       "rural health centre", "dispensary", "veterinary hospital", "civil hospital", "brick kiln factory"})


def _text(value: str | None) -> str:
    text = (value or "").lower().replace("_", " ").replace("-", " ").replace("&", " and ")
    return f" {re.sub(r'[^a-z0-9 ]+', ' ', text)} ".replace("  ", " ")


def is_generic_name(name: str | None) -> bool:
    """A name that only says what the place is ("Brick Kiln", "Medical Store"): never evidence on its own."""
    if not name:
        return True
    return re.sub(r"[^a-z ]+", "", name.lower()).strip() in GENERIC_NAME_WORDS


@lru_cache(maxsize=50_000)
def classify_label(label: str | None) -> Category | None | bool:
    """The canonical category for a source label; False when the label is a non-business; None when unknown."""
    if not label:
        return None
    text = _text(label)
    if text.strip() in _EXACT_EXCLUDED or (_EXCLUDED_RE.search(text) and not _NOT_EXCLUDED_RE.search(text)):
        return False
    return next((category for category, pattern in RULES if pattern.search(text)), None)


def classify_name(name: str | None) -> Category | None | bool:
    """The category a business name clearly states, False for a place of worship or graveyard, else None."""
    if not name:
        return None
    text = _text(name)
    if _NAME_EXCLUDED_RE.search(text) and not _NAME_EXCLUDED_UNLESS_RE.search(text):
        return False
    return next((category for category, pattern in _NAME_RULES_RE if pattern.search(text)), None)


def canonical_category(category: str | None, category_key: str | None, group: str | None,
                       name: str | None, evidence: bool = True) -> tuple[str, str, str] | None:
    """(canonical key, label, group) for one record, or None when it is not a business.

    The label decides; when it is missing or generic, a clear statement in the name refines it; a few strong name
    statements (a marriage hall, a brick kiln) win over any label. Unknown labels fall back to their group's general
    category, so no business is left without one. `evidence` says whether anything else shows the record is a
    business (contact details, or a source that only lists businesses): a record with no label, nothing in its
    name and no such evidence is a place name ("Chak No 52", "Sial Mor Interchange"), not a business.
    """
    from_label = classify_label(category) if category else None
    if from_label is None and category_key and category_key != category:
        from_label = classify_label(category_key)
    from_name = classify_name(name)
    if from_name is False and (from_label is None or (isinstance(from_label, Category) and from_label.generic)):
        return None
    if from_label is False:
        if isinstance(from_name, Category) and from_name.key in _NAME_OVERRIDES:
            from_label = from_name
        else:
            return None
    chosen: Category | None = from_label if isinstance(from_label, Category) else None
    if isinstance(from_name, Category) and (chosen is None or chosen.generic or from_name.key in _NAME_OVERRIDES):
        chosen = from_name
    if chosen is None or chosen.generic:
        # The name read with the label rules ("Daastan Clothing", "Healing Hub Rehabilitation Center"); only a
        # specific answer counts, and a name never excludes a place this way.
        from_name_words = classify_label(name) if name else None
        if isinstance(from_name_words, Category) and not from_name_words.generic:
            chosen = from_name_words
    if chosen is None:
        if not category and not evidence:
            return None
        chosen = CATEGORIES[GROUP_FALLBACK.get(group or "", "other_services")]
    return chosen.key, chosen.label, chosen.group
