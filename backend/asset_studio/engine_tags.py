"""What a generation IS and what it DEPICTS, so a person can find it again.

Two facets, both plain strings on the record.

`type` — what kind of thing was made:
    asset    code that builds one thing (the ordinary forge run)
    scene    code that builds a place: lights, fog, many things
    picture  a forge run kept without its code — a picture, not an asset
    sheet    a review contact sheet of a running game

`subject` — what it depicts: character, creature, building, prop, vehicle, environment, weapon,
ui, effect, material, test, other.

Where the subject comes from, in order of trust: a person's choice in the window (never
overwritten), the tags an agent passed to the forge, and only then a guess from the label, the
names the code gives its parts, and the material names. The guess is a weighted keyword vote —
deliberately simple, deterministic and cheap, because it runs on every record the history lists.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

SUBJECTS = ("character", "creature", "building", "prop", "vehicle", "environment", "weapon",
            "ui", "effect", "material", "test", "other")
TYPES = ("asset", "scene", "picture", "sheet")

# The words that DISCRIMINATE. Anatomy shared by people and beasts is kept apart (below), so a
# "head" never decides between a knight and a dragon.
_KEYS: dict[str, tuple[str, ...]] = {
    "character": ("character", "hero", "player", "npc", "human", "person", "man", "woman", "girl", "boy",
                  "knight", "warrior", "soldier", "zombie", "robot", "villager", "enemy", "boss", "avatar",
                  "humanoid", "wizard", "mage", "archer", "ninja", "pirate", "guard", "king", "queen",
                  "princess", "prince", "farmer", "worker", "goblin", "orc", "elf", "dwarf", "skeleton",
                  "mannequin", "doll", "hair", "helmet", "armor", "armour", "cape", "boots", "glove"),
    "creature": ("creature", "monster", "beast", "dino", "dinosaur", "dragon", "lizard", "trex", "rex",
                 "species", "anky", "ankylo", "bronto", "brachio", "diplo", "stego", "steggo", "trike", "tricera",
                 "ptero", "spino", "dilo", "pachy", "velociraptor", "mosasaur", "plesio", "raptor", "mob", "critter",
                 "animal", "dog", "cat", "bird", "fish", "spider", "snake", "wolf", "bear",
                 "horse", "cow", "pig", "sheep", "chicken", "duck", "frog", "turtle", "crab", "shark",
                 "whale", "capybara", "brainrot", "slime", "blob", "bug", "insect", "bee", "ant",
                 "tail", "claw", "fang", "wing", "sail", "maw", "tooth", "teeth", "tongue", "nostril",
                 "snout", "fur", "scale", "scales", "skin", "belly", "sclera", "iris", "horn", "hoof",
                 "paw", "beak", "feather", "fin", "tentacle", "shell"),
    "building": ("building", "house", "home", "tower", "castle", "wall", "bridge", "shop", "hut", "temple",
                 "church", "barn", "shed", "roof", "door", "window", "chimney", "brick", "fence", "gate",
                 "pillar", "column", "arch", "stairs", "staircase", "floor", "ceiling", "room", "city",
                 "town", "village", "street", "road", "factory", "warehouse", "station", "garage",
                 "cabin", "tent", "windmill", "lighthouse", "fort", "ruin", "ruins", "well", "dock"),
    "prop": ("prop", "crate", "barrel", "chest", "lamp", "lantern", "table", "chair", "bench", "potion",
             "coin", "key", "bottle", "cup", "mug", "book", "sign", "signpost", "flag", "banner", "torch",
             "candle", "bucket", "rope", "ladder", "wheel", "gear", "cog", "pot", "vase", "plate", "egg",
             "gem", "crystal", "orb", "scroll", "sack", "bag", "backpack", "shelf", "bed", "desk",
             "stool", "cart", "anvil", "cauldron", "pipe", "pickup", "collectible"),
    "vehicle": ("vehicle", "car", "truck", "ship", "boat", "plane", "aircraft", "airplane", "jet", "tank",
                "bike", "bicycle", "motorbike", "motorcycle", "wagon", "train", "rocket", "spaceship",
                "spacecraft", "ufo", "submarine", "helicopter", "kart", "bus", "van", "tractor", "sled",
                "raft", "canoe", "hovercraft", "drone", "mech"),
    "environment": ("environment", "terrain", "tree", "trees", "rock", "rocks", "grass", "mountain", "hill",
                    "sky", "water", "cloud", "clouds", "forest", "island", "cave", "river", "lake", "sea",
                    "ocean", "sand", "desert", "snow", "ice", "lava", "ground", "field", "plant", "flower",
                    "bush", "mushroom", "stone", "boulder", "cliff", "path", "arena", "level", "map",
                    "world", "stage", "track", "landscape", "skybox", "biome", "garden", "park", "beach",
                    "swamp", "jungle", "volcano", "platform", "pad", "belt", "ramp"),
    "weapon": ("weapon", "sword", "blade", "gun", "rifle", "pistol", "axe", "bow", "arrow", "shield",
               "dagger", "knife", "spear", "hammer", "mallet", "club", "bat", "wrench", "mace", "staff", "wand",
               "cannon", "turret", "missile", "bomb", "grenade", "katana", "scythe", "crossbow", "blaster",
               "launcher", "bullet", "pickaxe", "sledge"),
    "ui": ("ui", "hud", "button", "icon", "menu", "panel", "cursor", "logo", "badge", "font", "title",
           "score", "healthbar", "minimap", "toolbar", "dialog", "popup", "tooltip", "slider", "joystick"),
    "effect": ("effect", "effects", "vfx", "fx", "explosion", "fire", "smoke", "spark", "sparks", "trail",
               "particle", "particles", "glow", "beam", "laser", "magic", "spell", "splash", "dust", "flash",
               "shockwave", "aura", "lightning", "rain", "portal", "ripple", "confetti"),
    "material": ("material", "materials", "texture", "textures", "matcap", "shader", "pbr", "albedo",
                 "normalmap", "roughness", "metalness", "tiling", "pattern", "swatch", "palette", "decal"),
    "test": ("test", "tests", "probe", "sweep", "baseline", "sanity", "harness", "demo", "sample", "fixture",
             "tmp", "scratch", "debug", "compare", "comparison", "variant", "variants", "roundtrip", "round",
             "trip", "check", "bench", "benchmark", "experiment", "ab", "fresh", "solo", "thing", "cube",
             "sphere", "box", "cubes", "spheres", "boxes", "primitive", "primitives", "pass", "passes"),
}
# Anatomy that people and beasts share: a vote for BOTH, so it counts against "other" but never
# decides between them.
_BODY = ("body", "head", "arm", "arms", "leg", "legs", "torso", "hand", "hands", "face", "foot", "feet",
         "eye", "eyes", "mouth", "neck", "chest", "back", "shoulder", "knee", "elbow", "finger", "toe")

_SCENE_WORDS = ("scene", "level", "arena", "map", "world", "stage", "room", "environment", "landscape",
                "terrain", "city", "town", "village", "biome", "island")
_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CODE_NAMES = re.compile(
    r"""function\s+([A-Za-z_]\w*)|(?:const|let|var)\s+([A-Za-z_]\w*)\s*=|\.name\s*=\s*['"]([^'"]{1,60})['"]"""
    r"""|new\s+pc\.Entity\(\s*['"]([^'"]{1,60})['"]|name\s*:\s*['"]([^'"]{1,60})['"]""")


def tokens(text: str) -> list[str]:
    """Words of a label, an identifier or a file name: split on punctuation AND camelCase, lowercased."""
    if not text:
        return []
    return [t for t in _SPLIT.split(_CAMEL.sub(" ", str(text)).lower()) if len(t) > 1]


_PLURAL = {"enemies": "enemy", "species": "species", "wolves": "wolf", "knives": "knife", "leaves": "leaf",
           "elves": "elf", "dwarves": "dwarf", "bodies": "body", "trophies": "trophy"}


def _forms(w: str) -> tuple[str, ...]:
    """The word, and its singular if it looks plural: "creatures" votes as "creature", "dinos" as "dino"."""
    if w in _PLURAL:
        return (w, _PLURAL[w])
    if len(w) > 3 and w.endswith("ies"):
        return (w, w[:-3] + "y")
    if len(w) > 4 and w.endswith("es") and w[-3] in "sxz":
        return (w, w[:-2])
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return (w, w[:-1])
    return (w,)


def _vote(words: Iterable[str], weight: float, score: dict[str, float]) -> None:
    for raw in words:
        for w in _forms(raw):
            hit = False
            for subj, keys in _KEYS.items():
                if w in keys:
                    score[subj] = score.get(subj, 0.0) + weight
                    hit = True
            if w in _BODY:
                score["character"] = score.get("character", 0.0) + weight * 0.5
                score["creature"] = score.get("creature", 0.0) + weight * 0.5
                hit = True
            if hit:
                break


def code_names(code: str) -> list[str]:
    """The names a piece of asset code gives its parts, functions and variables."""
    out: list[str] = []
    for m in _CODE_NAMES.finditer(code or ""):
        for g in m.groups():
            if g:
                out.append(g)
    return out


def guess_subject(label: str = "", code: str = "", material_names: Optional[Iterable[str]] = None,
                  context: Optional[Iterable[str]] = None) -> tuple[str, float]:
    """The best-supported subject and its score; ("other", 0) when nothing says anything.

    `context` is what surrounds the thing — the table it sits in, the file, the folder. It weighs
    a third of the name: a "creatures" folder shelves an unknown name, but never outvotes a known one."""
    score: dict[str, float] = {}
    # The label outweighs everything, and its LAST word a little more than its first: in "stone
    # tower" the tower is the thing and the stone is what it is made of.
    for i, w in enumerate(tokens(label)):
        _vote([w], 3.0 + 0.05 * i, score)
    names = code_names(code)
    _vote((t for n in names for t in tokens(n)), 1.0, score)
    _vote((t for n in (material_names or []) for t in tokens(str(n))), 2.0, score)
    _vote((t for c in (context or []) for t in tokens(str(c))), 1.0, score)
    if not score:
        return "other", 0.0
    # "test" only wins outright — a "test" of a dino is still a dino to the person looking for it.
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    best, s = ranked[0]
    if best == "test" and len(ranked) > 1 and ranked[1][1] >= 3.0:
        best, s = ranked[1]
    return best, s


def guess_type(kind: str, label: str = "", code: str = "", has_code: Optional[bool] = None) -> str:
    if kind == "review":
        return "sheet"
    if has_code is None:
        has_code = bool(code)
    if not has_code:
        return "picture"
    words = set(tokens(label))
    if words & set(_SCENE_WORDS):
        return "scene"
    c = code or ""
    lights = len(re.findall(r"Light\s*\(", c)) + len(re.findall(r"addComponent\(\s*['\"]light['\"]", c))
    if "new THREE.Scene(" in c or re.search(r"\bFog(Exp2)?\s*\(", c) or ".fog" in c or lights >= 2:
        return "scene"
    return "asset"


def normalise_tags(tags: Optional[Iterable[str]]) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        s = _SPLIT.sub("-", str(t).strip().lower()).strip("-")
        if s and s not in out:
            out.append(s)
    return out[:12]


def classify(kind: str, label: str = "", code: str = "", material_names: Optional[Iterable[str]] = None,
             tags: Optional[Iterable[str]] = None, category: str = "", has_code: Optional[bool] = None) -> dict:
    """Type, subject, who decided the subject, and the clean tag list, for a new record."""
    tg = normalise_tags(list(tags or []) + ([category] if category else []))
    subject, by = "", "auto"
    for t in tg:
        if t in SUBJECTS:
            subject, by = t, "agent"
            break
    if not subject:
        subject, _ = guess_subject(label, code, material_names)
    return {"type": guess_type(kind, label, code, has_code), "subject": subject, "subject_by": by, "tags": tg}


def apply(rec: dict) -> bool:
    """Fill a stored forge record's facets in place. A person's or an agent's subject stands; only a
    missing or auto guess is (re)made. Returns True when something changed and should be saved."""
    changed = False
    st = rec.get("stats") or {}
    has_code = bool(rec.get("code"))
    t = guess_type(rec.get("kind") or "forge", rec.get("label") or "", rec.get("code") or "", has_code)
    if rec.get("type") != t:
        rec["type"] = t
        changed = True
    if not isinstance(rec.get("tags"), list):
        rec["tags"] = []
        changed = True
    by = rec.get("subject_by") or ""
    if by not in ("user", "agent") or rec.get("subject") not in SUBJECTS:
        for tg in rec.get("tags") or []:
            if tg in SUBJECTS:
                rec["subject"], rec["subject_by"] = tg, "agent"
                return True
        subject, _ = guess_subject(rec.get("label") or "", rec.get("code") or "", st.get("material_names") or [])
        if rec.get("subject") != subject or by != "auto":
            rec["subject"], rec["subject_by"] = subject, "auto"
            changed = True
    return changed
