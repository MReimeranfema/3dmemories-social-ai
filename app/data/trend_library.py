"""
Trend-Bibliothek — 3D Memories Social AI.

Statische, manuell kuratierte Trenddaten für Phase 1.
Kein Scraping, keine Websuche — bewusst einfach gehalten.

Struktur:
  HOOK_PATTERNS          — 15 bewährte Hook-Architekturen mit Vorlagen
  WATCHTIME_PATTERNS     — 8 Watchtime-Optimierungsformate
  MEME_STRUCTURES        — 8 aktuell funktionierende Meme-Formate
  TRANSFORMATION_TRENDS  — 8 Transformationsnarrative (3DM-spezifisch)
  PLATFORM_TRENDS        — Plattform-spezifische Empfehlungen (IG + FB)

Erweiterung:
  Neue Einträge einfach an die jeweilige Liste anhängen.
  Versionsnummer in LIBRARY_VERSION erhöhen.
  In Phase 2 wird diese Datei durch einen Web-Scraper-Agent ersetzt.
"""

from __future__ import annotations

from dataclasses import dataclass, field


LIBRARY_VERSION = "1.0.0"


# ── Datenklassen ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HookPattern:
    """
    Bewährtes Hook-Muster mit Vorlage und Beispiel.

    virality_score:  0–10, empirisch eingeschätzt
    emotion:         dominante Emotion, die dieser Hook auslöst
    category:        Typ-Klassifikation
    best_platforms:  Plattformen, auf denen dieser Typ besonders stark konvertiert
    """
    name: str
    template: str           # {X}, {Y} als Platzhalter
    example: str            # Konkretes 3DM-Beispiel
    virality_score: float
    emotion: str
    category: str
    best_platforms: list[str]
    notes: str = ""


@dataclass(frozen=True)
class WatchtimePattern:
    """
    Format-Architektur für maximale Watchtime.

    ideal_length_sec:  Empfohlene Videolänge in Sekunden
    retention_curve:   Qualitative Beschreibung des Abbruchverhaltens
    """
    name: str
    structure: str          # Ablaufbeschreibung (Schritt für Schritt)
    ideal_length_sec: int
    retention_curve: str    # "steep_drop", "gradual_hold", "cliff_end"
    best_platforms: list[str]
    content_types: list[str]
    example: str
    notes: str = ""


@dataclass(frozen=True)
class MemeStructure:
    """
    Aktuell funktionierende Meme-/Format-Struktur.

    viral_ceiling:  Einschätzung des Viral-Potenzials ("high", "medium", "niche")
    """
    name: str
    format_template: str    # Wörtliche Formatbeschreibung
    example: str            # 3DM-Beispiel
    viral_ceiling: str      # "high" | "medium" | "niche"
    best_platforms: list[str]
    emotion: str
    notes: str = ""


@dataclass(frozen=True)
class TransformationTrend:
    """
    Transformationsnarrative spezifisch für 3D Memories.
    Diese beschreiben den 'Vorher → Nachher'-Bogen, der im Content sichtbar ist.
    """
    name: str
    input_format: str       # Was ist die Ausgangssituation?
    output_format: str      # Was ist das Ergebnis?
    emotional_core: str     # Welches Gefühl treibt die Transformation?
    hook_potential: float   # 0–10
    best_content_types: list[str]
    example_hook: str
    notes: str = ""


@dataclass(frozen=True)
class PlatformTrend:
    """Plattform-spezifische Trend-Empfehlung."""
    platform: str
    format_priority: list[str]     # Content-Typen, Priorität absteigend
    optimal_length_sec: dict       # {"reel": 30, "story": 15, ...}
    tone: str                      # Kurzcharakterisierung des erwarteten Tons
    trending_topics: list[str]     # Aktuell performante Themen (simuliert)
    avoid: list[str]               # Was gerade nicht funktioniert
    notes: str = ""


# ── Hook-Patterns ──────────────────────────────────────────────────────────────

HOOK_PATTERNS: list[HookPattern] = [

    HookPattern(
        name="Schock-Reversal",
        template="Ich hätte nie gedacht, dass {X} so {Y} sein kann.",
        example="Ich hätte nie gedacht, dass eine Kinderzeichnung so eine Wirkung haben kann.",
        virality_score=8.5,
        emotion="Überraschung",
        category="schock",
        best_platforms=["instagram", "tiktok"],
        notes="Funktioniert, weil der Leser seine eigene Erwartung reflektiert. Auflösung muss liefern.",
    ),

    HookPattern(
        name="Geheimnis-Enthüllung",
        template="Der Grund, warum {X} {Y} bedeutet, hat mich umgehauen.",
        example="Der Grund, warum dieses Tier aus Ton so viel bedeutet, hat mich umgehauen.",
        virality_score=7.8,
        emotion="Neugier",
        category="geheimnis",
        best_platforms=["instagram", "facebook"],
        notes="Klassischer Curiosity-Gap. Titel verrät nicht die Antwort. Wirkt auf FB besonders bei 35+.",
    ),

    HookPattern(
        name="Widerspruchs-Hook",
        template="Alle reden über {X}, aber niemand spricht über {Y}.",
        example="Alle reden über Fotos, aber niemand spricht darüber, dass Fotos verblassen.",
        virality_score=8.2,
        emotion="Provokation",
        category="widerspruch",
        best_platforms=["instagram", "tiktok"],
        notes="Positioniert den Creator als Insider. Braucht starke Auflösung im Video.",
    ),

    HookPattern(
        name="Nostalgie-Trigger",
        template="Vor {X} Jahren hat {Y}. Das vergisst man nicht.",
        example="Vor 4 Jahren hat mein Sohn dieses Bild gemalt. Das vergisst man nicht.",
        virality_score=9.0,
        emotion="Nostalgie",
        category="nostalgie",
        best_platforms=["facebook", "instagram"],
        notes="Höchstes Share-Potenzial auf Facebook. Eltern identifizieren sich sofort.",
    ),

    HookPattern(
        name="Transformations-Versprechen",
        template="So sah {X} aus, bevor {Y} passiert ist.",
        example="So sah die Zeichnung aus, bevor sie real wurde.",
        virality_score=8.7,
        emotion="Vorfreude",
        category="transformation",
        best_platforms=["instagram", "tiktok"],
        notes="Before/After schlägt fast alles. Slow reveal maximiert Watchtime.",
    ),

    HookPattern(
        name="Einbezug-Frage",
        template="Weißt du noch, wie sich {X} angefühlt hat?",
        example="Weißt du noch, wie sich das erste Bild deines Kindes angefühlt hat?",
        virality_score=7.5,
        emotion="Rührung",
        category="frage",
        best_platforms=["facebook", "instagram"],
        notes="Hohe Kommentar-Rate. Eltern schreiben von eigenen Erlebnissen. Gut für Reichweite.",
    ),

    HookPattern(
        name="Konkret-Zahl",
        template="{Zahl} Jahre später — und {X} ist immer noch {Y}.",
        example="7 Jahre später — und diese Figur steht immer noch auf dem Schreibtisch.",
        virality_score=7.2,
        emotion="Beständigkeit",
        category="zahl",
        best_platforms=["facebook", "instagram"],
        notes="Zahlen stoppen den Scroll. '7 Jahre' klingt echter als '10 Jahre'. Spezifisch > rund.",
    ),

    HookPattern(
        name="POV-Immersion",
        template="POV: Du öffnest eine Schachtel und {X}.",
        example="POV: Du öffnest die Schachtel. Dein Kind sieht seine Figur zum ersten Mal.",
        virality_score=8.9,
        emotion="Aufregung",
        category="pov",
        best_platforms=["instagram", "tiktok"],
        notes="POV ist ein eigenes Genre. Funktioniert als Hook nur wenn der POV ungewohnt ist.",
    ),

    HookPattern(
        name="Moment-Einfrieren",
        template="Dieser Moment dauerte {X} Sekunden. Er hält jetzt für immer.",
        example="Dieser Moment dauerte 3 Sekunden. Er hält jetzt für immer.",
        virality_score=8.1,
        emotion="Wärme",
        category="moment",
        best_platforms=["instagram", "facebook"],
        notes="Kontrast zwischen flüchtigem Moment und Dauerhaftigkeit. Philosophisch, aber zugänglich.",
    ),

    HookPattern(
        name="Vergleichs-Kontrast",
        template="Das Foto zeigt den Moment. {Produkt} macht ihn greifbar.",
        example="Das Foto zeigt das Lachen. Die Figur macht es greifbar.",
        virality_score=7.6,
        emotion="Erkenntnis",
        category="vergleich",
        best_platforms=["instagram", "facebook"],
        notes="Kein direktes Produktnaming nötig. Psychologie: Was ich anfassen kann, glaube ich mehr.",
    ),

    HookPattern(
        name="Eltern-Identifikation",
        template="Wenn du Elternteil bist, kennst du diesen Moment.",
        example="Wenn du Elternteil bist, kennst du das: Das erste Bild, das wirklich etwas ist.",
        virality_score=8.4,
        emotion="Zugehörigkeit",
        category="identifikation",
        best_platforms=["facebook", "instagram"],
        notes="In-Group-Signalling. Wer kein Elternteil ist, scrollt weiter — das ist gut.",
    ),

    HookPattern(
        name="Verlust-Bewusstsein",
        template="Eines Tages werden sie nicht mehr {X}. Heute tun sie es noch.",
        example="Eines Tages werden sie nicht mehr malen. Heute tun sie es noch.",
        virality_score=9.2,
        emotion="Dringlichkeit",
        category="verlust",
        best_platforms=["instagram", "facebook"],
        notes="Höchste emotionale Intensität. Vorsichtig einsetzen — wirkt aber bei Eltern extrem stark.",
    ),

    HookPattern(
        name="Überraschungs-Reaktion",
        template="Das hat niemand erwartet — am wenigsten {X}.",
        example="Das hat niemand erwartet — am wenigsten mein Kind selbst.",
        virality_score=7.9,
        emotion="Überraschung",
        category="ueberraschung",
        best_platforms=["instagram", "tiktok"],
        notes="Reaktions-Videos performen. Authentische Kinderreaktionen sind unschlagbar.",
    ),

    HookPattern(
        name="Zukunfts-Zeitreise",
        template="In {Zahl} Jahren wird {X} diesen Moment sehen und {Y}.",
        example="In 20 Jahren wird er diesen Moment sehen und verstehen, was wir fühlten.",
        virality_score=8.6,
        emotion="Sentimentalität",
        category="zeitreise",
        best_platforms=["facebook", "instagram"],
        notes="Zukunftsperspektive + Nostalgie-Pre-Loading. Starkes Save-Signal.",
    ),

    HookPattern(
        name="Universelle-Wahrheit",
        template="Manche Dinge sind zu wichtig, um sie dem Vergessen zu überlassen.",
        example="Manche Dinge sind zu wichtig, um sie dem Vergessen zu überlassen.",
        virality_score=7.3,
        emotion="Nachdenklichkeit",
        category="wahrheit",
        best_platforms=["facebook", "instagram"],
        notes="Statement-Hook ohne Frage. Wirkt als Eröffnung eines längeren Carousel-Posts.",
    ),
]


# ── Watchtime-Patterns ─────────────────────────────────────────────────────────

WATCHTIME_PATTERNS: list[WatchtimePattern] = [

    WatchtimePattern(
        name="Slow-Reveal",
        structure=(
            "0–5s: Hook mit Frage oder unvollständigem Bild. "
            "5–20s: Prozess zeigen (Schritt für Schritt), Auflösung wird zurückgehalten. "
            "20–30s: Großer Reveal. Emotion kommt DANN. "
            "30–35s: Reaktion oder Text-Overlay mit Kernbotschaft."
        ),
        ideal_length_sec=35,
        retention_curve="gradual_hold",
        best_platforms=["instagram", "tiktok"],
        content_types=["reel", "short"],
        example="Kinderzeichnung wird gezeigt → Arbeitsprozess in Zeitraffer → fertige 3D-Figur → Kinderreaktion",
        notes="Höchste Retention durch verzögerten Reward. Letzter Frame entscheidet über Share.",
    ),

    WatchtimePattern(
        name="Reaktions-Bogen",
        structure=(
            "0–3s: Person sieht Paket an — weiß noch nicht was drin ist. "
            "3–10s: Auspacken in Echtzeit, echte Reaktion. "
            "10–20s: Erklärung durch Person selbst ('Das ist...') "
            "20–25s: Nahaufnahme des Produkts. "
            "25–30s: Emotionaler Abschluss."
        ),
        ideal_length_sec=30,
        retention_curve="cliff_end",
        best_platforms=["instagram", "tiktok", "facebook"],
        content_types=["reel", "short", "video"],
        example="Mutter öffnet Paket, sieht die Figur ihrer Tochter — sieht sie zum ersten Mal",
        notes="Authentizität ist alles. Nicht nochmal filmen wenn die echte Reaktion schon auf Video ist.",
    ),

    WatchtimePattern(
        name="Vorher-Nachher-Kontrast",
        structure=(
            "0–5s: 'Vorher' zeigen — Kinderzeichnung, Foto, Ausgangszustand. "
            "5–8s: Übergangsmoment (Schnitt, Wipe, Drehung). "
            "8–20s: 'Nachher' aus verschiedenen Winkeln — mit Musik-Klimax. "
            "20–30s: Side-by-Side-Vergleich oder Zoom auf Detail."
        ),
        ideal_length_sec=30,
        retention_curve="gradual_hold",
        best_platforms=["instagram", "tiktok"],
        content_types=["reel", "image", "carousel"],
        example="Kinderzeichnung (Papier) → Schnitt → identische 3D-Figur aus gleichem Winkel",
        notes="Der Übergangsmoment ist der wichtigste Schnitt. Musik-Peak muss auf den Reveal fallen.",
    ),

    WatchtimePattern(
        name="Prozess-Zeitraffer",
        structure=(
            "0–5s: Fertige Figur zeigen — Ergebnis zuerst. "
            "5–25s: Zeitraffer des Entstehungsprozesses rückwärts erzählt. "
            "25–35s: Ausgangsbild (Zeichnung/Foto). "
            "35–40s: Seite an Seite: Original + Ergebnis."
        ),
        ideal_length_sec=40,
        retention_curve="gradual_hold",
        best_platforms=["instagram", "facebook"],
        content_types=["reel", "video"],
        example="Fertige Hasenfigur → Herstellungsprozess → Originalfoto des Haustiers",
        notes="Ergebnis zuerst verhindert Early-Drop. Prozess ist fesselnd aber nur wenn Ergebnis schon überzeugt.",
    ),

    WatchtimePattern(
        name="Emotionaler-Eskalations-Bogen",
        structure=(
            "0–5s: Neutraler Einstieg — alltäglicher Moment. "
            "5–15s: Steigerung — eine Person, ein Moment, eine Erinnerung. "
            "15–25s: Emotionaler Höhepunkt — Tränen, Lachen, Sprachlosigkeit. "
            "25–30s: Stille oder minimale Musik — lässt die Emotion wirken."
        ),
        ideal_length_sec=30,
        retention_curve="cliff_end",
        best_platforms=["facebook", "instagram"],
        content_types=["reel", "video"],
        example="Großmutter schaut eine Kinderzeichnung an → erkennt darin ihr verstorbenes Enkelbild → weint",
        notes="Am schwersten zu inszenieren, aber höchstes Share-Potenzial. Niemals stellen, immer dokumentieren.",
    ),

    WatchtimePattern(
        name="Tutorial-Enthüllung",
        structure=(
            "0–5s: Frage oder Problem ('Wie macht man...?'). "
            "5–30s: Schritt-für-Schritt-Anleitung mit Text-Overlays. "
            "30–40s: Endergebnis. "
            "40–45s: CTA: 'Zeigt uns eure Zeichnung.'"
        ),
        ideal_length_sec=45,
        retention_curve="gradual_hold",
        best_platforms=["instagram", "facebook"],
        content_types=["reel", "carousel"],
        example="Wie schicke ich ein Bild für eine 3D-Figur? — 4 Schritte erklärt",
        notes="Funktioniert nur wenn der Prozess tatsächlich interessant ist. Kein Fake-Tutorial.",
    ),

    WatchtimePattern(
        name="Dual-Perspektive",
        structure=(
            "0–5s: Person A sieht das Produkt (Elternteil). "
            "5–12s: Person B sieht das Produkt (Kind). "
            "12–20s: Beide Reaktionen im Wechsel. "
            "20–25s: Gemeinsamer Moment, beide im Bild."
        ),
        ideal_length_sec=25,
        retention_curve="gradual_hold",
        best_platforms=["instagram", "facebook"],
        content_types=["reel"],
        example="Mutter und Kind öffnen gemeinsam das Paket — verschiedene Blickwinkel geschnitten",
        notes="Zwei Reaktionen verdoppeln die emotionale Bandbreite. Schnitte müssen sauber sein.",
    ),

    WatchtimePattern(
        name="Detail-Zoom-Journey",
        structure=(
            "0–3s: Gesamtansicht der Figur. "
            "3–15s: Extreme Nahaufnahmen von Details (Hände, Augen, Textur). "
            "15–20s: Zurückzoomen auf das Ganze. "
            "20–25s: Original-Zeichnung daneben — Detail-Vergleich."
        ),
        ideal_length_sec=25,
        retention_curve="steep_drop",
        best_platforms=["instagram"],
        content_types=["reel", "carousel"],
        example="Figur einer Katze — Zoom auf Fell-Textur, Augen, Pfoten — zurück zur Gesamtansicht",
        notes="Zeigt Handwerksqualität besser als jede Erklärung. Licht ist entscheidend.",
    ),
]


# ── Meme-Strukturen ───────────────────────────────────────────────────────────

MEME_STRUCTURES: list[MemeStructure] = [

    MemeStructure(
        name="POV-Format",
        format_template="POV: [ungewöhnliche Situation aus Ich-Perspektive]",
        example="POV: Dein Kind sieht seine Zeichnung zum ersten Mal als echtes 3D-Objekt.",
        viral_ceiling="high",
        best_platforms=["instagram", "tiktok"],
        emotion="Immersion",
        notes="POV ist dann stark, wenn die Situation wirklich besonders ist. Alltag funktioniert nicht.",
    ),

    MemeStructure(
        name="'Das Einzige was zählt'-Format",
        format_template="Alle reden über [Ablenkung]. Das Einzige was zählt: [emotionale Wahrheit].",
        example="Alle reden über das perfekte Foto. Das Einzige was zählt: Erinnerungen die man anfassen kann.",
        viral_ceiling="medium",
        best_platforms=["facebook", "instagram"],
        emotion="Klarheit",
        notes="Positioniert eine höhere Wahrheit gegen gesellschaftlichen Lärm. Gut für text-heavy Carousel.",
    ),

    MemeStructure(
        name="Before/After-Subversion",
        format_template="Vorher: [Erwartung]. Nachher: [unerwartetes Ergebnis mit mehr Tiefe].",
        example="Vorher: Ein Stück Papier. Nachher: Der Moment, den dein Kind nie vergessen wird.",
        viral_ceiling="high",
        best_platforms=["instagram", "tiktok"],
        emotion="Überraschung",
        notes="Das 'Nachher' muss besser sein als erwartet — und anders als der Standard.",
    ),

    MemeStructure(
        name="'Tell me without telling me'-Format",
        format_template="Zeig mir [X], ohne mir [X] zu zeigen. [visuelles Ergebnis]",
        example="Zeig mir, dass du jemanden liebst, ohne es zu sagen. [Figur der geliebten Person]",
        viral_ceiling="medium",
        best_platforms=["instagram", "tiktok"],
        emotion="Komplicität",
        notes="Interaktives Format. Funktioniert als Kommentar-Magnet.",
    ),

    MemeStructure(
        name="'Things that just make sense'-Format",
        format_template="Dinge, die einfach Sinn ergeben: [Liste mit unerwarteten Einträgen]",
        example="Dinge, die einfach Sinn ergeben: Kinderzeichnungen sind zu gut für die Schublade.",
        viral_ceiling="medium",
        best_platforms=["instagram", "tiktok"],
        emotion="Zustimmung",
        notes="Save-Magnet durch List-Format. Letzter Eintrag ist der stärkste — dort kommt das Produkt.",
    ),

    MemeStructure(
        name="Rating-Format",
        format_template="[Objekt/Moment] bewertet mit [Zahl]/10: [Begründung mit Twist].",
        example="Diese Kinderzeichnung bewertet mit 11/10: Weil kein erwachsener Künstler so sieht.",
        viral_ceiling="medium",
        best_platforms=["instagram", "tiktok"],
        emotion="Humor",
        notes="Leicht zu konsumieren. Der Twist ist alles — ohne ihn wird es generisch.",
    ),

    MemeStructure(
        name="Reaktions-Split-Screen",
        format_template="Person A reagiert auf [X]. Person B reagiert auf [X]. Unterschied: [emotionale Pointe].",
        example="Kind sieht Figur: [Schreien vor Freude]. Elternteil sieht Kind: [Tränen].",
        viral_ceiling="high",
        best_platforms=["instagram", "facebook", "tiktok"],
        emotion="Empathie",
        notes="Zwei Emotionsebenen gleichzeitig. Technisch einfach, emotional komplex.",
    ),

    MemeStructure(
        name="'Ich hätte nie gedacht'-Testimonial",
        format_template="Ich hätte nie gedacht, dass [X] so [Y] sein könnte. Dann ist [Z] passiert.",
        example="Ich hätte nie gedacht, dass ein Stück Ton so viel bedeuten kann. Dann hat meine Mutter geweint.",
        viral_ceiling="high",
        best_platforms=["facebook", "instagram"],
        emotion="Authentizität",
        notes="Klingt nach echtem Erlebnisbericht. Kommentar-Konversion sehr hoch.",
    ),
]


# ── Transformations-Trends ────────────────────────────────────────────────────

TRANSFORMATION_TRENDS: list[TransformationTrend] = [

    TransformationTrend(
        name="Kinderzeichnung-zu-3D",
        input_format="Kinderzeichnung auf Papier (Buntstift, Wachsmalkreide, Filzstift)",
        output_format="Handgefertigte 3D-Figur in Ton oder Kunstharz",
        emotional_core="Wunder — das Unmögliche wird möglich",
        hook_potential=9.5,
        best_content_types=["reel", "short"],
        example_hook="Mein Kind hat das gezeichnet. Ich habe es real gemacht.",
        notes="Kern-USP von 3DM. Immer das Bild und die Figur im selben Frame zeigen.",
    ),

    TransformationTrend(
        name="Haustier-zu-Skulptur",
        input_format="Foto des Haustieres (lebendig oder verstorben)",
        output_format="Handgefertigte 3D-Tier-Skulptur",
        emotional_core="Unsterblichkeit — etwas Geliebtes bleibt",
        hook_potential=9.0,
        best_content_types=["reel", "video"],
        example_hook="Er ist nicht mehr da. Aber er ist noch hier.",
        notes="Verstorbene Haustiere haben die stärkste emotionale Resonanz. Ethisch einfühlsam bleiben.",
    ),

    TransformationTrend(
        name="Familienmoment-zu-Diorama",
        input_format="Familienfoto oder beschriebener Moment",
        output_format="Miniatur-Diorama der Familie in diesem Moment",
        emotional_core="Zeitstillstand — ein Moment wird zu einem Objekt",
        hook_potential=8.2,
        best_content_types=["reel", "carousel"],
        example_hook="So haben wir ausgesehen, als alles perfekt war.",
        notes="Aufwändiger zu produzieren, dafür höherer Preisakzeptanz beim Endkunden.",
    ),

    TransformationTrend(
        name="Handschrift-zu-3D-Relief",
        input_format="Handgeschriebene Notiz, Brief oder Name eines Kindes",
        output_format="3D-Relief der Handschrift in Ton oder Metall",
        emotional_core="Vergänglichkeit stoppen — Schrift die bleibt",
        hook_potential=7.8,
        best_content_types=["reel", "image"],
        example_hook="Das war das letzte was sie geschrieben hat.",
        notes="Besonders stark bei Trauer-Kontext. Vorsichtig und würdevoll inszenieren.",
    ),

    TransformationTrend(
        name="Baby-Abdruck-Skulptur",
        input_format="Handabdruck oder Fußabdruck eines Babys",
        output_format="3D-Skulptur des Abdrucks in Ton",
        emotional_core="Zartheit konservieren — was schon fast zu klein für Worte ist",
        hook_potential=8.8,
        best_content_types=["reel", "image"],
        example_hook="Die Hand war so klein, dass ich kaum glauben konnte, dass sie echt war.",
        notes="Klassisches Baby-Content-Format. Funktioniert besonders als Geschenk-Idee für Großeltern.",
    ),

    TransformationTrend(
        name="Lieblingsstofftier-Klon",
        input_format="Altes Kuscheltier oder Lieblingsstofftier des Kindes",
        output_format="Originalgetreue 3D-Nachbildung",
        emotional_core="Erhaltung — was schon abgenutzt ist, wird für immer konserviert",
        hook_potential=8.5,
        best_content_types=["reel", "video"],
        example_hook="Dieses Stofftier ist auseinandergefallen. Aber nicht für immer.",
        notes="Extrem hohe Empathie bei Eltern die selbst ein Kindheitsstofftier hatten.",
    ),

    TransformationTrend(
        name="Erstes-Bild-Unsterblichkeit",
        input_format="Das erste Bild eines Kindes (Foto, Gemälde, Zeichnung)",
        output_format="3D-Figur oder Relief des ersten Bildes",
        emotional_core="Zeitkapsel — der erste Moment eines Lebens",
        hook_potential=8.0,
        best_content_types=["carousel", "reel"],
        example_hook="Das erste Bild, das er je gemacht hat. Es hängt jetzt auch in 3D.",
        notes="'Erstes' ist immer ein starkes emotionales Anker-Wort.",
    ),

    TransformationTrend(
        name="Gezeichnetes-Alter-Ego",
        input_format="Selbstporträt-Zeichnung eines Kindes (wie das Kind sich selbst sieht)",
        output_format="3D-Figur des Selbstporträts",
        emotional_core="Selbst-Ausdruck ehren — die eigene Welt ernst nehmen",
        hook_potential=8.6,
        best_content_types=["reel", "short"],
        example_hook="So sieht mein Kind sich selbst. Und genau so sieht es aus.",
        notes="Starker Perspective-Twist. Kind als Künstler würdigen, nicht als niedlich abtun.",
    ),
]


# ── Plattform-Trends ──────────────────────────────────────────────────────────

PLATFORM_TRENDS: list[PlatformTrend] = [

    PlatformTrend(
        platform="instagram",
        format_priority=["reel", "carousel", "story", "image"],
        optimal_length_sec={"reel": 30, "story": 15, "short": 60},
        tone=(
            "Emotional, visuell, authentisch — aber nicht kitschig. "
            "Cineastisch in der Bildsprache, persönlich in der Sprache. "
            "Weniger erklären, mehr fühlen lassen."
        ),
        trending_topics=[
            "Elternschaft und Erinnerungen",
            "Handwerk und Authentizität",
            "Kleine Momente mit großer Bedeutung",
            "Tierliebe und Verlust",
            "Kinderkunst als echte Kunst",
            "Minimalismus und emotionale Gegenstände",
        ],
        avoid=[
            "Übermäßig inszenierte Familienbilder",
            "Explizite Kaufaufforderungen im ersten Frame",
            "Zu lange Texte im Video",
            "Stockfotos und generisch wirkende Visuals",
            "Mehrere CTAs in einem Post",
        ],
        notes="Reels haben auf IG derzeit 3x mehr Reichweite als statische Posts. Carousel hat höchste Save-Rate.",
    ),

    PlatformTrend(
        platform="facebook",
        format_priority=["video", "reel", "image", "carousel"],
        optimal_length_sec={"video": 60, "reel": 45, "short": 30},
        tone=(
            "Wärmer, familiärer, etwas ruhiger im Tempo. "
            "Nostalgie und Gemeinschaft resonieren stärker als auf IG. "
            "Längere Texte werden hier noch gelesen — Geschichten erlaubt."
        ),
        trending_topics=[
            "Großeltern und Enkelsachen",
            "Verstorbene Haustiere und Gedenken",
            "Nostalgie der eigenen Kindheit",
            "Handgemachte vs. industrielle Produkte",
            "Familientraditionen",
            "Geschenkideen für besondere Anlässe",
        ],
        avoid=[
            "Zu schnelle Schnitte (Zielgruppe 35+ bevorzugt langsamer)",
            "Trendy Musik ohne Bezug zum Inhalt",
            "Ironie ohne klare Auflösung",
            "Zu junges Bildsprache-Vokabular",
        ],
        notes=(
            "FB-Zielgruppe ist 35–60+. Emotionale Tiefe schlägt visuellen Schnitt. "
            "Kommentar-Engagement ist auf FB höher — auf offene Fragen setzen."
        ),
    ),
]


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def get_hooks_by_category(category: str) -> list[HookPattern]:
    """Gibt alle Hook-Patterns einer bestimmten Kategorie zurück."""
    return [h for h in HOOK_PATTERNS if h.category == category]


def get_hooks_by_platform(platform: str) -> list[HookPattern]:
    """Gibt alle Hook-Patterns zurück, die für die gegebene Plattform geeignet sind."""
    return [h for h in HOOK_PATTERNS if platform in h.best_platforms]


def get_top_hooks(n: int = 5) -> list[HookPattern]:
    """Gibt die n Hook-Patterns mit dem höchsten Virality-Score zurück."""
    return sorted(HOOK_PATTERNS, key=lambda h: h.virality_score, reverse=True)[:n]


def get_watchtime_patterns_for_platform(platform: str) -> list[WatchtimePattern]:
    """Gibt Watchtime-Patterns zurück, die für die gegebene Plattform geeignet sind."""
    return [w for w in WATCHTIME_PATTERNS if platform in w.best_platforms]


def get_platform_trend(platform: str) -> PlatformTrend | None:
    """Gibt den Plattform-Trend für die gegebene Plattform zurück."""
    return next((p for p in PLATFORM_TRENDS if p.platform == platform), None)


def get_top_transformations(n: int = 3) -> list[TransformationTrend]:
    """Gibt die n Transformationstrends mit dem höchsten Hook-Potenzial zurück."""
    return sorted(
        TRANSFORMATION_TRENDS, key=lambda t: t.hook_potential, reverse=True
    )[:n]
