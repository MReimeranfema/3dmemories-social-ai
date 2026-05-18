"""
ImagePromptBuilder — Prompt-Assemblierung aus ContentBriefRecord.

Spezifikation: image-generation/prompt-builder.md

Ablauf:
  1. Brief-Felder extrahieren (Hook, Emotion, Scene, Visual, Camera, Brand)
  2. Template für platform × content_type auswählen
  3. 8 Komponenten befüllen (SUBJECT, SETTING, LIGHTING, CAMERA, EMOTION, STYLE, BRAND, QUALITY)
  4. CI-Prüfung (GDPR, Preis-Schutz, Brand-Schutz)
  5. Positivprompt + Negativprompt assemblieren
  6. Token-Budget prüfen + ggf. kürzen
  7. BuiltPrompt zurückgeben

Template-Version: 1.0.0
Negativprompt-Version: 1.0.0
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from app.models.content_brief import ContentBriefRecord
from app.models.image_generation import BuiltPrompt, get_platform_format

log = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Konstanten
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_VERSION = "1.0.0"

# Token-Budget pro Komponente (Wörter, approximiert)
TOKEN_BUDGET: dict[str, int] = {
    "SUBJECT": 40,
    "SETTING": 30,
    "LIGHTING": 25,
    "CAMERA": 20,
    "EMOTION": 20,
    "STYLE": 30,
    "BRAND": 25,
    "QUALITY": 20,
}
MAX_TOTAL_TOKENS = 200
MAX_NEGATIVE_TOKENS = 120

# Standard-Negativprompt (immer aktiv)
BASE_NEGATIVE_PROMPT = (
    "deformed, distorted, disfigured, bad anatomy, extra limbs, extra fingers, "
    "missing fingers, fused fingers, mutation, ugly, blurry, low quality, "
    "watermark, signature, text overlay, logo, brand name, price tag, "
    "stock photo look, clinical, sterile, corporate, cold blue lighting, "
    "harsh white background, neon colors, oversaturated, underexposed, "
    "overexposed, noise, grain, jpeg artifacts, pixelated, cropped badly, "
    "out of frame, tiling, repeating pattern"
)

# Gesicht-Schutz (immer aktiv — GDPR)
GDPR_NEGATIVE_TOKENS = (
    "child face, children face, kids face, young face, baby face, "
    "toddler face, recognizable child, child portrait"
)

# Preis-Schutz (immer aktiv)
PRICE_NEGATIVE_TOKENS = (
    "price tag, euro sign, dollar sign, sale label, discount badge, "
    "promotional text, offer text, advertisement overlay"
)

# Lichtstimmung: Emotion → Licht-Preset
EMOTION_LIGHTING_MAP: dict[str, str] = {
    "nostalgie":     "warm golden hour light, soft amber glow, gentle backlighting",
    "nostalgia":     "warm golden hour light, soft amber glow, gentle backlighting",
    "freude":        "bright diffused daylight, soft natural light, cheerful illumination",
    "joy":           "bright diffused daylight, soft natural light, cheerful illumination",
    "trauer":        "moody soft light, muted grey tones, overcast window light",
    "sadness":       "moody soft light, muted grey tones, overcast window light",
    "rührung":       "warm candlelight glow, intimate soft light, gentle rim light",
    "emotion":       "warm candlelight glow, intimate soft light, gentle rim light",
    "stolz":         "warm side light, dramatic but gentle, crafted studio feel",
    "pride":         "warm side light, dramatic but gentle, crafted studio feel",
    "staunen":       "magical soft light, slight haze, wonder-inducing glow",
    "wonder":        "magical soft light, slight haze, wonder-inducing glow",
    "liebe":         "rose-tinted warm light, bokeh background, intimate glow",
    "love":          "rose-tinted warm light, bokeh background, intimate glow",
    "verbundenheit": "warm earth tones, soft directional light, shared glow",
    "verbindung":    "warm earth tones, soft directional light, shared glow",
}

DEFAULT_LIGHTING = "warm soft natural light, golden hour, cozy atmosphere"

# Kamerastimmung: Story-Akt → Kamera-Preset
STORY_CAMERA_MAP: dict[str, str] = {
    "drei_akt":      "medium close-up, 50mm lens equivalent, slight shallow depth of field",
    "zwei_akt":      "close-up, 85mm lens equivalent, soft bokeh background",
    "reveal":        "wide to close pull-in, macro detail shot, dramatic reveal angle",
    "transformation":"side angle tracking shot, 35mm lens, motion implied",
    "moment":        "intimate close-up, 85mm portrait lens, eye-level perspective",
}

DEFAULT_CAMERA = "close-up shot, 50mm lens equivalent, shallow depth of field"

# Plattform-Safe-Zone-Hinweise
PLATFORM_COMPOSITION_HINTS: dict[tuple[str, str], str] = {
    ("instagram", "reel_thumbnail"):  "main subject centered in middle third, clear safe zone top and bottom",
    ("instagram", "reel"):            "main subject centered in middle third, clear safe zone top and bottom",
    ("instagram", "feed_post"):       "rule of thirds composition, subject filling 60% of frame",
    ("instagram", "story_image"):     "main subject centered vertically, avoid top 15% and bottom 15%",
    ("instagram", "carousel_slide"):  "centered square composition, subject dominant in frame",
    ("facebook",  "feed_post"):       "horizontal composition, subject left-aligned, breathing room right",
    ("facebook",  "story_image"):     "vertical composition, centered subject, safe zone respected",
    ("facebook",  "reel"):            "vertical composition, centered subject, safe zone respected",
}

DEFAULT_COMPOSITION = "centered composition, subject clearly visible, breathing room around subject"

# Brand-Style-Tokens (immer aktiv)
BRAND_STYLE_BASE = (
    "handcrafted aesthetic, artisanal quality, warm earth tones, "
    "textured surfaces, authentic and personal, not stock photo, "
    "emotional storytelling, crafted with love"
)

# Qualitäts-Tokens (Basis)
QUALITY_BASE = (
    "photorealistic, high detail, sharp focus on subject, "
    "professional photography quality, natural colors, clean composition"
)

QUALITY_BOOST = (
    "ultra photorealistic, masterful photography, razor sharp detail, "
    "award winning composition, perfect lighting, cinematic quality"
)


# ─────────────────────────────────────────────────────────────────────────────
# ImagePromptBuilder
# ─────────────────────────────────────────────────────────────────────────────

class ImagePromptBuilder:
    """
    Assembliert einen standardisierten Bildprompt aus einem ContentBriefRecord.

    Standardisiert, wiederholbar, CI-konform.
    Alle Ausgaben sind deterministisch gegeben denselben Input-Brief.
    """

    def build(
        self,
        brief: ContentBriefRecord,
        platform: str | None = None,
        content_type: str | None = None,
        quality_boost: bool = False,
        extra_negative_tokens: list[str] | None = None,
    ) -> BuiltPrompt:
        """
        Baut einen vollständigen BuiltPrompt aus einem validierten Brief.

        Args:
            brief:                 Validierter ContentBriefRecord (production_blocked=FALSE erwartet)
            platform:              Override für brief.platform (optional)
            content_type:          Override für Content-Typ (optional)
            quality_boost:         True → erhöhte Qualitäts-Tokens
            extra_negative_tokens: Zusätzliche Negativprompt-Tokens (bei Retries aus QC-Fehlern)

        Returns:
            BuiltPrompt mit positivem Prompt, Negativprompt, Format, CI-Status

        Raises:
            ValueError: Kritische Felder fehlen im Brief
        """
        start_ms = int(time.monotonic() * 1000)

        resolved_platform     = (platform or brief.platform or "instagram").lower()
        resolved_content_type = (content_type or "feed_post").lower()

        # Aus brief_json extrahieren
        brief_data = brief.brief_json.get("brief", {})

        # ── Komponenten befüllen ───────────────────────────────────────────────
        components = self._build_components(
            brief=brief,
            brief_data=brief_data,
            platform=resolved_platform,
            content_type=resolved_content_type,
            quality_boost=quality_boost,
        )

        # ── CI-Prüfung ─────────────────────────────────────────────────────────
        ci_violations = self._check_ci(components, brief_data)
        ci_passed = len(ci_violations) == 0

        if not ci_passed:
            log.warning(
                "prompt_builder.ci_violations",
                brief_id=brief.brief_id,
                violations=ci_violations,
            )

        # ── Positiv-Prompt assemblieren ────────────────────────────────────────
        positive_prompt = self._assemble_positive(components)

        # ── Negativ-Prompt assemblieren ────────────────────────────────────────
        negative_prompt = self._assemble_negative(brief_data, extra_negative_tokens)

        # ── Token-Budget prüfen ────────────────────────────────────────────────
        positive_prompt = self._trim_to_budget(positive_prompt, MAX_TOTAL_TOKENS)
        negative_prompt = self._trim_to_budget(negative_prompt, MAX_NEGATIVE_TOKENS)

        # ── Format bestimmen ───────────────────────────────────────────────────
        aspect_ratio, width, height, mime_type = get_platform_format(
            resolved_platform, resolved_content_type
        )

        build_duration_ms = int(time.monotonic() * 1000) - start_ms
        prompt_id = f"PROMPT-{uuid.uuid4().hex[:12].upper()}"

        log.info(
            "prompt_builder.built",
            brief_id=brief.brief_id,
            platform=resolved_platform,
            content_type=resolved_content_type,
            aspect_ratio=aspect_ratio,
            positive_tokens=self._count_tokens(positive_prompt),
            negative_tokens=self._count_tokens(negative_prompt),
            ci_passed=ci_passed,
            build_ms=build_duration_ms,
        )

        return BuiltPrompt(
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            platform=resolved_platform,
            content_type=resolved_content_type,
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
            mime_type=mime_type,
            template_version=TEMPLATE_VERSION,
            components=components,
            ci_checks_passed=ci_passed,
            ci_violations=ci_violations,
            token_count_positive=self._count_tokens(positive_prompt),
            token_count_negative=self._count_tokens(negative_prompt),
            build_duration_ms=build_duration_ms,
            prompt_id=prompt_id,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Komponenten-Befüllung
    # ─────────────────────────────────────────────────────────────────────────

    def _build_components(
        self,
        brief: ContentBriefRecord,
        brief_data: dict,
        platform: str,
        content_type: str,
        quality_boost: bool,
    ) -> dict[str, str]:
        """Baut alle 8 Prompt-Komponenten."""
        emotion = (brief.primary_emotion or "").lower()
        hook    = brief.hook or ""

        # ── SUBJECT: Was ist im Bild? ──────────────────────────────────────────
        subject = self._build_subject(brief_data, hook)

        # ── SETTING: Umgebung / Hintergrund ───────────────────────────────────
        setting = self._build_setting(brief_data)

        # ── LIGHTING: Lichtstimmung ────────────────────────────────────────────
        lighting = EMOTION_LIGHTING_MAP.get(emotion, DEFAULT_LIGHTING)

        # ── CAMERA: Kamerawinkel + Stil ────────────────────────────────────────
        story_type = (brief.story_structure or "drei_akt").lower()
        camera_from_brief = self._extract_camera_style(brief_data)
        camera = camera_from_brief or STORY_CAMERA_MAP.get(story_type, DEFAULT_CAMERA)

        # ── EMOTION: Emotionale Zielwirkung ───────────────────────────────────
        emotion_token = self._build_emotion_token(emotion, brief_data)

        # ── STYLE: Visueller Stil ─────────────────────────────────────────────
        style = self._build_style(brief_data, brief.visual_style)

        # ── BRAND: Marken-Compliance ──────────────────────────────────────────
        brand = self._build_brand(brief_data)

        # ── QUALITY: Technische Qualität ──────────────────────────────────────
        composition_hint = PLATFORM_COMPOSITION_HINTS.get(
            (platform, content_type), DEFAULT_COMPOSITION
        )
        quality = f"{QUALITY_BOOST if quality_boost else QUALITY_BASE}, {composition_hint}"

        return {
            "SUBJECT":  subject,
            "SETTING":  setting,
            "LIGHTING": lighting,
            "CAMERA":   camera,
            "EMOTION":  emotion_token,
            "STYLE":    style,
            "BRAND":    brand,
            "QUALITY":  quality,
        }

    def _build_subject(self, brief_data: dict, hook: str) -> str:
        """Leitet SUBJECT aus Scene-Struktur + Hook ab."""
        scenes = brief_data.get("scene_structure") or []
        if scenes and isinstance(scenes, list):
            # Erste Szene als Hauptmotiv
            first_scene = scenes[0] if scenes else {}
            desc = first_scene.get("description", "") if isinstance(first_scene, dict) else ""
            if desc:
                return desc

        # Fallback: Hook-basiert
        if hook:
            # Kürzen auf Wesentliches
            return hook[:80] if len(hook) > 80 else hook

        # Generic Fallback
        return (
            "children's drawing being transformed into a 3D figurine, "
            "handcrafted artisan product, close-up of delicate craftsmanship"
        )

    def _build_setting(self, brief_data: dict) -> str:
        """Leitet SETTING aus visueller Stimmung ab."""
        visual_mood = brief_data.get("visual_mood") or {}
        if isinstance(visual_mood, dict):
            texture = visual_mood.get("texture_feel", "")
            setting_notes = visual_mood.get("setting", "")
            if setting_notes:
                return setting_notes
            if texture:
                return f"warm domestic interior, {texture} textures, natural materials"

        return (
            "cozy home interior, wooden table surface, soft natural light from window, "
            "warm background with subtle texture"
        )

    def _extract_camera_style(self, brief_data: dict) -> str | None:
        """Extrahiert Kamera-Stil aus Brief wenn vorhanden."""
        camera = brief_data.get("camera_style") or {}
        if isinstance(camera, dict):
            primary = camera.get("primary", "")
            movement = camera.get("movement", "")
            if primary:
                parts = [primary]
                if movement:
                    parts.append(movement)
                return ", ".join(parts)
        return None

    def _build_emotion_token(self, emotion: str, brief_data: dict) -> str:
        """Übersetzt die geforderte Emotion in visuelle Wirkungsparameter."""
        act1_target = ""
        story = brief_data.get("story_structure") or {}
        if isinstance(story, dict):
            act1 = story.get("act_1") or {}
            if isinstance(act1, dict):
                act1_target = act1.get("emotional_target", "")

        base_emotion = emotion.capitalize() if emotion else "emotional"
        if act1_target:
            return f"evoking {base_emotion}, viewer feels {act1_target}, authentic emotional moment"
        return f"evoking {base_emotion}, genuine and heartfelt, emotional resonance"

    def _build_style(self, brief_data: dict, visual_style: str | None) -> str:
        """Baut den STYLE-Block aus brief_json."""
        style_parts = []

        visual_mood = brief_data.get("visual_mood") or {}
        if isinstance(visual_mood, dict):
            if visual_mood.get("texture_feel"):
                style_parts.append(visual_mood["texture_feel"])
            if visual_mood.get("color_palette"):
                style_parts.append(visual_mood["color_palette"])

        if visual_style:
            style_parts.append(visual_style)

        brand_style = brief_data.get("brand_style") or {}
        if isinstance(brand_style, dict):
            tone = brand_style.get("tone", "")
            if tone:
                tone_readable = tone.replace("_", " ")
                style_parts.append(tone_readable)

        if not style_parts:
            style_parts = ["warm artisanal style, handcrafted aesthetic, earth tones"]

        return ", ".join(style_parts[:4])  # Max 4 Style-Tokens

    def _build_brand(self, brief_data: dict) -> str:
        """Baut den BRAND-Compliance-Block."""
        brand_style = brief_data.get("brand_style") or {}
        do_list: list[str] = []
        if isinstance(brand_style, dict):
            dos = brand_style.get("do") or []
            if isinstance(dos, list):
                do_list = [str(d) for d in dos[:3]]  # Max 3

        base = BRAND_STYLE_BASE
        if do_list:
            base += ", " + ", ".join(do_list)
        return base

    # ─────────────────────────────────────────────────────────────────────────
    # Prompt-Assemblierung
    # ─────────────────────────────────────────────────────────────────────────

    def _assemble_positive(self, components: dict[str, str]) -> str:
        """
        Assembliert den finalen Positiv-Prompt aus den 8 Komponenten.
        Reihenfolge: SUBJECT → SETTING → LIGHTING → CAMERA → EMOTION → STYLE → BRAND → QUALITY
        """
        parts = [
            components["SUBJECT"],
            components["SETTING"],
            components["LIGHTING"],
            components["CAMERA"],
            components["EMOTION"],
            components["STYLE"],
            components["BRAND"],
            components["QUALITY"],
        ]
        # Leere Teile herausfiltern
        return ", ".join(p.strip() for p in parts if p.strip())

    def _assemble_negative(
        self,
        brief_data: dict,
        extra_negative_tokens: list[str] | None = None,
    ) -> str:
        """
        Assembliert den Negativ-Prompt aus Base + GDPR + Preis + Brief-spezifischen Dont-Tokens.

        extra_negative_tokens werden bei Retries aus QC-Fehlercodes hinzugefügt
        (übergeben von ImageJobRunner nach fehlgeschlagenem QC-Durchlauf).
        """
        parts = [BASE_NEGATIVE_PROMPT, GDPR_NEGATIVE_TOKENS, PRICE_NEGATIVE_TOKENS]

        # Brief-spezifische DONT-Tokens aus brand_style.dont
        brand_style = brief_data.get("brand_style") or {}
        if isinstance(brand_style, dict):
            donts = brand_style.get("dont") or []
            if isinstance(donts, list) and donts:
                parts.append(", ".join(str(d) for d in donts[:5]))

        # Retry-Tokens aus QC-Fehlercodes (max 5 Einträge, um Token-Budget zu schonen)
        if extra_negative_tokens:
            for token_str in extra_negative_tokens[:5]:
                if token_str and token_str.strip():
                    parts.append(token_str.strip())

        return ", ".join(p for p in parts if p.strip())

    # ─────────────────────────────────────────────────────────────────────────
    # CI-Prüfung
    # ─────────────────────────────────────────────────────────────────────────

    def _check_ci(self, components: dict[str, str], brief_data: dict) -> list[str]:
        """
        Prüft alle CI-Regeln. Gibt Liste der Verstöße zurück (leer = alles OK).
        """
        violations: list[str] = []
        full_positive = " ".join(components.values()).lower()

        # CI-01: Kein Preis im Prompt
        price_triggers = ["€", "euro", "preis", "sale", "rabatt", "angebot", "$"]
        for trigger in price_triggers:
            if trigger in full_positive:
                violations.append(
                    f"CI-01: Preisbezug im Prompt gefunden: '{trigger}'"
                )

        # CI-02: Keine direkten Gesichtsaufforderungen für Kinder
        child_face_triggers = ["child face", "children face", "kid face", "baby face", "kindergesicht"]
        for trigger in child_face_triggers:
            if trigger in full_positive:
                violations.append(
                    f"CI-02: GDPR-Verstoß — Kindergesicht im Prompt: '{trigger}'"
                )

        # CI-03: Brand-Ton muss warm/handgemacht sein
        cold_tokens = ["corporate", "industrial", "clinical", "sterile", "cold blue"]
        for token in cold_tokens:
            if token in full_positive:
                violations.append(
                    f"CI-03: Markenverstoß — verbotener Token im Prompt: '{token}'"
                )

        # CI-04: Kein fremdes Branding
        foreign_brands = ["ikea", "amazon", "apple", "google", "nike"]
        for brand in foreign_brands:
            if brand in full_positive:
                violations.append(
                    f"CI-04: Fremdes Branding im Prompt: '{brand}'"
                )

        return violations

    # ─────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Approximiert Token-Anzahl als Wort-Anzahl."""
        return len(text.split())

    @staticmethod
    def _trim_to_budget(text: str, max_tokens: int) -> str:
        """Kürzt Text auf max_tokens Wörter wenn nötig."""
        words = text.split()
        if len(words) <= max_tokens:
            return text
        trimmed = " ".join(words[:max_tokens])
        log.debug(
            "prompt_builder.trimmed",
            original_tokens=len(words),
            trimmed_tokens=max_tokens,
        )
        return trimmed
