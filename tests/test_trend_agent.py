"""
Tests für TrendAgent und Trendbibliothek.

Testet:
  - Trendbibliothek-Integrität (alle Felder gesetzt, Scores im Bereich 0–10)
  - TrendAgent.get_trend_context() — Struktur und Plattform-Filter
  - TrendAgent.analyze_hook_strength() — Muster-Erkennung und Problemerkennung
  - TrendAgent.assess_virality() — Score-Berechnung und Meme-Match
  - TrendAgent.analyze_hook_batch() — Batch-Verarbeitung
  - TrendContext.to_prompt_addendum() — Prompt-Ausgabe
  - Integration TrendAgent → IdeenAgent — trend_context_used Flag

Keine externen Abhängigkeiten nötig:
  Alle Tests laufen ohne DB, Slack, Claude oder Webzugriff.
"""

import pytest

from app.data.trend_library import (
    HOOK_PATTERNS,
    MEME_STRUCTURES,
    PLATFORM_TRENDS,
    TRANSFORMATION_TRENDS,
    WATCHTIME_PATTERNS,
    get_hooks_by_category,
    get_hooks_by_platform,
    get_platform_trend,
    get_top_hooks,
    get_top_transformations,
    get_watchtime_patterns_for_platform,
)
from app.agents.trend_agent import TrendAgent, TrendContext, HookAnalysis, ViralityAssessment


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def agent() -> TrendAgent:
    return TrendAgent()


# ── Bibliotheks-Integritäts-Tests ─────────────────────────────────────────────

class TestTrendLibraryIntegrity:

    def test_hook_patterns_not_empty(self):
        assert len(HOOK_PATTERNS) >= 10, "Mindestens 10 Hook-Patterns erwartet"

    def test_hook_patterns_virality_in_range(self):
        for h in HOOK_PATTERNS:
            assert 0.0 <= h.virality_score <= 10.0, (
                f"Hook '{h.name}': virality_score {h.virality_score} außerhalb 0–10"
            )

    def test_hook_patterns_have_required_fields(self):
        for h in HOOK_PATTERNS:
            assert h.name, f"Hook hat keinen Namen"
            assert h.template, f"Hook '{h.name}' hat keine Vorlage"
            assert h.example, f"Hook '{h.name}' hat kein Beispiel"
            assert h.emotion, f"Hook '{h.name}' hat keine Emotion"
            assert h.category, f"Hook '{h.name}' hat keine Kategorie"
            assert len(h.best_platforms) >= 1, f"Hook '{h.name}' hat keine Plattformen"

    def test_hook_examples_within_80_chars(self):
        """Alle Hook-Beispiele müssen selbst ≤ 80 Zeichen sein (Vorbild-Funktion)."""
        too_long = [h for h in HOOK_PATTERNS if len(h.example) > 80]
        assert len(too_long) == 0, (
            f"{len(too_long)} Hook-Beispiele > 80 Zeichen: "
            + ", ".join(f"'{h.name}' ({len(h.example)} Zeichen)" for h in too_long)
        )

    def test_watchtime_patterns_not_empty(self):
        assert len(WATCHTIME_PATTERNS) >= 5

    def test_watchtime_ideal_length_positive(self):
        for w in WATCHTIME_PATTERNS:
            assert w.ideal_length_sec > 0, f"'{w.name}': ideal_length_sec muss > 0 sein"
            assert w.ideal_length_sec <= 300, f"'{w.name}': ideal_length_sec > 300 unrealistisch"

    def test_meme_structures_not_empty(self):
        assert len(MEME_STRUCTURES) >= 5

    def test_meme_viral_ceiling_valid(self):
        valid = {"high", "medium", "niche"}
        for m in MEME_STRUCTURES:
            assert m.viral_ceiling in valid, (
                f"MemeStructure '{m.name}': viral_ceiling '{m.viral_ceiling}' ungültig"
            )

    def test_transformation_trends_not_empty(self):
        assert len(TRANSFORMATION_TRENDS) >= 5

    def test_transformation_hook_potential_in_range(self):
        for t in TRANSFORMATION_TRENDS:
            assert 0.0 <= t.hook_potential <= 10.0, (
                f"TransformationTrend '{t.name}': hook_potential {t.hook_potential} außerhalb 0–10"
            )

    def test_platform_trends_cover_instagram_and_facebook(self):
        platforms = {p.platform for p in PLATFORM_TRENDS}
        assert "instagram" in platforms
        assert "facebook" in platforms

    def test_platform_trends_have_avoid_list(self):
        for pt in PLATFORM_TRENDS:
            assert len(pt.avoid) >= 1, f"PlatformTrend '{pt.platform}' hat keine Avoid-Liste"


# ── Hilfsfunktionen-Tests ─────────────────────────────────────────────────────

class TestTrendLibraryHelpers:

    def test_get_hooks_by_platform_instagram(self):
        hooks = get_hooks_by_platform("instagram")
        assert len(hooks) >= 5
        for h in hooks:
            assert "instagram" in h.best_platforms

    def test_get_hooks_by_platform_facebook(self):
        hooks = get_hooks_by_platform("facebook")
        assert len(hooks) >= 5
        for h in hooks:
            assert "facebook" in h.best_platforms

    def test_get_hooks_by_category_nostalgie(self):
        hooks = get_hooks_by_category("nostalgie")
        assert len(hooks) >= 1
        for h in hooks:
            assert h.category == "nostalgie"

    def test_get_top_hooks_returns_correct_count(self):
        top5 = get_top_hooks(5)
        assert len(top5) == 5

    def test_get_top_hooks_sorted_by_virality(self):
        top5 = get_top_hooks(5)
        scores = [h.virality_score for h in top5]
        assert scores == sorted(scores, reverse=True), "Top-Hooks nicht nach Virality sortiert"

    def test_get_platform_trend_instagram(self):
        pt = get_platform_trend("instagram")
        assert pt is not None
        assert pt.platform == "instagram"

    def test_get_platform_trend_unknown_returns_none(self):
        pt = get_platform_trend("myspace")
        assert pt is None

    def test_get_top_transformations(self):
        top3 = get_top_transformations(3)
        assert len(top3) == 3
        potentials = [t.hook_potential for t in top3]
        assert potentials == sorted(potentials, reverse=True)

    def test_get_watchtime_patterns_for_platform(self):
        patterns = get_watchtime_patterns_for_platform("instagram")
        assert len(patterns) >= 3
        for p in patterns:
            assert "instagram" in p.best_platforms


# ── TrendAgent.get_trend_context() ────────────────────────────────────────────

class TestTrendAgentContext:

    def test_context_instagram_returns_correct_platform(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert ctx.platform == "instagram"

    def test_context_facebook_returns_correct_platform(self, agent):
        ctx = agent.get_trend_context("facebook")
        assert ctx.platform == "facebook"

    def test_context_all_returns_all_platform(self, agent):
        ctx = agent.get_trend_context("all")
        assert ctx.platform == "all"

    def test_context_has_hooks(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert len(ctx.top_hooks) >= 3

    def test_context_has_watchtime_patterns(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert len(ctx.top_watchtime_patterns) >= 1

    def test_context_has_meme_structures(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert len(ctx.top_meme_structures) >= 1

    def test_context_has_transformations(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert len(ctx.top_transformations) >= 1

    def test_context_platform_trend_set_for_known_platform(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert ctx.platform_trend is not None
        assert ctx.platform_trend.platform == "instagram"

    def test_context_platform_trend_none_for_all(self, agent):
        ctx = agent.get_trend_context("all")
        # "all" hat keinen dedizierten PlatformTrend-Eintrag
        assert ctx.platform_trend is None

    def test_context_respects_top_n_hooks(self, agent):
        ctx = agent.get_trend_context("instagram", top_n_hooks=3)
        assert len(ctx.top_hooks) == 3

    def test_context_generated_at_set(self, agent):
        ctx = agent.get_trend_context("instagram")
        assert ctx.generated_at is not None

    def test_context_randomize_produces_valid_results(self, agent):
        """Randomize=True darf die Struktur nicht kaputt machen."""
        ctx = agent.get_trend_context("instagram", randomize=True)
        assert len(ctx.top_hooks) >= 1
        assert len(ctx.top_watchtime_patterns) >= 1

    def test_context_multiple_calls_consistent_count(self, agent):
        """Gleiche Parameter → gleiche Anzahl Ergebnisse."""
        ctx1 = agent.get_trend_context("instagram", top_n_hooks=4, randomize=False)
        ctx2 = agent.get_trend_context("instagram", top_n_hooks=4, randomize=False)
        assert len(ctx1.top_hooks) == len(ctx2.top_hooks)


# ── TrendContext.to_prompt_addendum() ─────────────────────────────────────────

class TestTrendContextPromptAddendum:

    def test_addendum_not_empty(self, agent):
        ctx = agent.get_trend_context("instagram")
        addendum = ctx.to_prompt_addendum()
        assert len(addendum) > 100

    def test_addendum_contains_hook_info(self, agent):
        ctx = agent.get_trend_context("instagram")
        addendum = ctx.to_prompt_addendum()
        assert "HOOK" in addendum or "hook" in addendum.lower()

    def test_addendum_contains_platform_info(self, agent):
        ctx = agent.get_trend_context("instagram")
        addendum = ctx.to_prompt_addendum()
        assert "INSTAGRAM" in addendum or "instagram" in addendum.lower()

    def test_addendum_contains_trend_hint(self, agent):
        ctx = agent.get_trend_context("instagram")
        addendum = ctx.to_prompt_addendum()
        assert "TREND" in addendum.upper()

    def test_addendum_contains_transformation_section(self, agent):
        ctx = agent.get_trend_context("instagram")
        addendum = ctx.to_prompt_addendum()
        assert "TRANSFORMATION" in addendum.upper()

    def test_addendum_contains_originality_note(self, agent):
        """Wichtige Regel: Trends sind Inspiration, keine Vorschrift."""
        ctx = agent.get_trend_context("instagram")
        addendum = ctx.to_prompt_addendum()
        assert "Inspiration" in addendum or "inspiration" in addendum.lower()

    def test_addendum_for_facebook_differs_from_instagram(self, agent):
        ctx_ig = agent.get_trend_context("instagram")
        ctx_fb = agent.get_trend_context("facebook")
        addendum_ig = ctx_ig.to_prompt_addendum()
        addendum_fb = ctx_fb.to_prompt_addendum()
        # Beide sollten unterschiedliche Plattform-Abschnitte haben
        assert addendum_ig != addendum_fb


# ── TrendAgent.analyze_hook_strength() ────────────────────────────────────────

class TestHookAnalysis:

    def test_analyse_returns_hookanalysis(self, agent):
        result = agent.analyze_hook_strength("Ich hätte nie gedacht, dass ein Bild so viel bedeuten kann.")
        assert isinstance(result, HookAnalysis)

    def test_nostalgie_hook_detected(self, agent):
        result = agent.analyze_hook_strength("Vor 5 Jahren hat mein Kind dieses Bild gemalt.")
        assert result.detected_pattern is not None
        assert result.detected_category is not None

    def test_pov_hook_detected(self, agent):
        result = agent.analyze_hook_strength("POV: Du öffnest die Schachtel und dein Kind weint vor Freude.")
        assert result.detected_pattern == "POV-Immersion"

    def test_advert_word_flagged_as_issue(self, agent):
        result = agent.analyze_hook_strength("Kaufen und sofort bestellen!")
        issues_lower = [i.lower() for i in result.issues]
        assert any("werbewörter" in issue or "kaufen" in issue for issue in issues_lower)

    def test_too_long_hook_flagged(self, agent):
        long_hook = "A" * 85
        result = agent.analyze_hook_strength(long_hook)
        assert any("lang" in i.lower() or "80" in i for i in result.issues)

    def test_too_short_hook_flagged(self, agent):
        result = agent.analyze_hook_strength("Kurz.")
        assert any("kurz" in i.lower() for i in result.issues)

    def test_clichee_hook_flagged(self, agent):
        result = agent.analyze_hook_strength("In diesem Video zeige ich euch, wie...")
        assert any("klischee" in i.lower() or "ankündigung" in i.lower() for i in result.issues)

    def test_good_hook_has_no_critical_issues(self, agent):
        result = agent.analyze_hook_strength(
            "Eines Tages werden sie nicht mehr malen. Heute tun sie es noch."
        )
        # Kein Werbewort, kein Klischee, gute Länge
        advert_issues = [i for i in result.issues if "werbewörter" in i.lower()]
        assert len(advert_issues) == 0

    def test_emotion_mismatch_reported(self, agent):
        # Nostalgie-Hook + andere Emotion
        result = agent.analyze_hook_strength(
            "Vor 5 Jahren hat mein Kind das gemalt.",
            idea_emotion="Wut",
        )
        # Suggestions sollten Hinweis auf Mismatch enthalten
        all_text = " ".join(result.suggestions).lower()
        assert len(result.suggestions) >= 1

    def test_alternative_templates_provided(self, agent):
        result = agent.analyze_hook_strength("Mein Kind hat gemalt.", platform="instagram")
        assert len(result.alternative_templates) >= 1

    def test_virality_score_in_range(self, agent):
        result = agent.analyze_hook_strength("Dieser Moment dauerte 3 Sekunden. Er bleibt für immer.")
        assert 0.0 <= result.estimated_virality <= 10.0


# ── TrendAgent.assess_virality() ──────────────────────────────────────────────

class TestViralityAssessment:

    def test_returns_virality_assessment(self, agent):
        result = agent.assess_virality(
            hook="POV: Du öffnest die Schachtel.",
            platform="instagram",
            content_type="reel",
        )
        assert isinstance(result, ViralityAssessment)

    def test_virality_score_in_range(self, agent):
        result = agent.assess_virality(
            hook="Vor 5 Jahren hat mein Hund so ausgesehen.",
            platform="instagram",
            content_type="reel",
        )
        assert 0.0 <= result.virality_score <= 10.0

    def test_pov_hook_gets_meme_match(self, agent):
        result = agent.assess_virality(
            hook="POV: Du siehst die Figur zum ersten Mal.",
            platform="instagram",
            content_type="reel",
        )
        assert result.matching_meme_structure == "POV-Format"

    def test_before_after_hook_gets_meme_match(self, agent):
        result = agent.assess_virality(
            hook="Vorher: Ein Blatt Papier. Nachher: Eine echte Figur.",
            platform="instagram",
            content_type="reel",
        )
        assert result.matching_meme_structure is not None

    def test_zeichnung_hook_gets_transformation_match(self, agent):
        result = agent.assess_virality(
            hook="Mein Kind hat diese Zeichnung gemalt.",
            platform="instagram",
            content_type="reel",
            transformation_input="Kinderzeichnung",
        )
        assert result.matching_transformation == "Kinderzeichnung-zu-3D"

    def test_haustier_hook_gets_transformation_match(self, agent):
        result = agent.assess_virality(
            hook="Unser Hund ist nicht mehr da. Aber er ist noch hier.",
            platform="facebook",
            content_type="video",
        )
        assert result.matching_transformation == "Haustier-zu-Skulptur"

    def test_format_recommendation_set(self, agent):
        result = agent.assess_virality(
            hook="Das ist mein Kind.",
            platform="instagram",
            content_type="image",
        )
        assert result.format_recommendation  # nicht leer

    def test_reasoning_not_empty(self, agent):
        result = agent.assess_virality(
            hook="Weißt du noch, wie sich das anfühlt?",
            platform="instagram",
            content_type="reel",
        )
        assert result.reasoning


# ── TrendAgent.analyze_hook_batch() ───────────────────────────────────────────

class TestBatchAnalysis:

    def test_batch_returns_same_count(self, agent):
        ideas = [
            {"hook": "Vor 5 Jahren hat mein Kind das gemalt.", "emotion": "Nostalgie",
             "plattform": "instagram", "content_type": "reel"},
            {"hook": "POV: Du siehst es zum ersten Mal.", "emotion": "Überraschung",
             "plattform": "instagram", "content_type": "reel"},
            {"hook": "Dieser Hund lebt nicht mehr. Aber er ist unsterblich.", "emotion": "Trauer",
             "plattform": "facebook", "content_type": "video"},
        ]
        enriched = agent.analyze_hook_batch(ideas, platform="instagram")
        assert len(enriched) == len(ideas)

    def test_batch_adds_trend_enriched_flag(self, agent):
        ideas = [{"hook": "Test-Hook", "emotion": "Freude",
                  "plattform": "instagram", "content_type": "reel"}]
        enriched = agent.analyze_hook_batch(ideas)
        assert enriched[0]["trend_enriched"] is True

    def test_batch_adds_hook_analysis(self, agent):
        ideas = [{"hook": "Vor 5 Jahren...", "emotion": "Nostalgie",
                  "plattform": "instagram", "content_type": "reel"}]
        enriched = agent.analyze_hook_batch(ideas)
        assert "hook_analysis" in enriched[0]
        assert isinstance(enriched[0]["hook_analysis"], HookAnalysis)

    def test_batch_adds_virality(self, agent):
        ideas = [{"hook": "Vor 5 Jahren...", "emotion": "Nostalgie",
                  "plattform": "instagram", "content_type": "reel"}]
        enriched = agent.analyze_hook_batch(ideas)
        assert "virality" in enriched[0]
        assert isinstance(enriched[0]["virality"], ViralityAssessment)

    def test_batch_does_not_modify_original(self, agent):
        original = {"hook": "Test", "emotion": "Freude",
                    "plattform": "instagram", "content_type": "reel"}
        ideas = [original.copy()]
        agent.analyze_hook_batch(ideas)
        # Original-Dict wurde nicht verändert
        assert "trend_enriched" not in original

    def test_empty_batch_returns_empty_list(self, agent):
        result = agent.analyze_hook_batch([])
        assert result == []


# ── Hook-Empfehlungen ──────────────────────────────────────────────────────────

class TestHookRecommendations:

    def test_recommendations_for_instagram(self, agent):
        hooks = agent.get_hook_recommendations("instagram", top_n=5)
        assert len(hooks) == 5
        for h in hooks:
            assert "instagram" in h.best_platforms

    def test_recommendations_filtered_by_emotion(self, agent):
        hooks = agent.get_hook_recommendations("instagram", emotion="Nostalgie", top_n=3)
        # Nostalgie-Hooks sollen vorne stehen
        assert len(hooks) >= 1
        if hooks[0].emotion == "Nostalgie":
            assert hooks[0].virality_score > 0

    def test_watchtime_recommendation_for_reel(self, agent):
        pattern = agent.get_watchtime_recommendation("instagram", "reel")
        assert pattern is not None
        assert "reel" in pattern.content_types or "instagram" in pattern.best_platforms


# ── Integration TrendAgent → IdeenAgent ───────────────────────────────────────

class TestIdeenAgentTrendIntegration:
    """Testet, dass IdeenAgent TrendAgent korrekt einbindet."""

    def test_ideen_agent_accepts_trend_agent(self):
        """IdeenAgent nimmt TrendAgent ohne Fehler an."""
        from app.agents.ideen_agent import IdeenAgent
        ta = TrendAgent()
        agent = IdeenAgent(db=None, trend_agent=ta)
        assert agent._trend_agent is ta

    def test_ideen_agent_auto_creates_trend_agent(self):
        """IdeenAgent erstellt TrendAgent automatisch wenn use_trend_context=True."""
        from app.agents.ideen_agent import IdeenAgent
        agent = IdeenAgent(db=None, use_trend_context=True)
        assert agent._trend_agent is not None

    def test_ideen_agent_no_trend_agent_when_disabled(self):
        """IdeenAgent hat keinen TrendAgent wenn use_trend_context=False."""
        from app.agents.ideen_agent import IdeenAgent
        agent = IdeenAgent(db=None, use_trend_context=False)
        assert agent._trend_agent is None

    def test_ideen_agent_run_sets_trend_context_used(self):
        """run() setzt trend_context_used=True wenn TrendAgent aktiv."""
        from app.agents.ideen_agent import IdeenAgent, IdeenAgentParams
        ta = TrendAgent()
        agent = IdeenAgent(db=None, trend_agent=ta)
        result = agent.run(IdeenAgentParams(platform="instagram", count=3, channel="C_TEST"))
        assert result.trend_context_used is True

    def test_ideen_agent_run_without_trend_agent(self):
        """run() funktioniert auch ohne TrendAgent — trend_context_used=False."""
        from app.agents.ideen_agent import IdeenAgent, IdeenAgentParams
        agent = IdeenAgent(db=None, use_trend_context=False)
        result = agent.run(IdeenAgentParams(platform="instagram", count=3, channel="C_TEST"))
        assert result.trend_context_used is False
        assert result.error is None

    def test_ideen_agent_prompt_contains_trend_info(self):
        """Wenn TrendAgent aktiv, muss der generierte Prompt Trend-Info enthalten."""
        from app.agents.ideen_agent import IdeenAgent
        ta = TrendAgent()
        agent = IdeenAgent(db=None, trend_agent=ta)

        ctx = ta.get_trend_context("instagram")
        system_prompt, _ = agent._build_prompt(
            platform="instagram",
            count=5,
            focus=None,
            exclude_ids=[],
            trend_context=ctx,
        )
        assert "TREND" in system_prompt.upper()
        assert "HOOK" in system_prompt.upper()

    def test_ideen_agent_prompt_without_trend_context_clean(self):
        """Ohne Trend-Kontext enthält der Prompt kein TREND-Addendum."""
        from app.agents.ideen_agent import IdeenAgent
        agent = IdeenAgent(db=None, use_trend_context=False)

        system_prompt, _ = agent._build_prompt(
            platform="instagram",
            count=5,
            focus=None,
            exclude_ids=[],
            trend_context=None,
        )
        # Kein Trend-Block im Prompt
        assert "TREND-KONTEXT" not in system_prompt


# ── Standalone-Ausführung ──────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Schnelltest ohne pytest:
      cd 3dmemories-social-ai
      python -m tests.test_trend_agent
    """
    agent = TrendAgent()

    print(f"\n{'='*65}")
    print("TREND-BIBLIOTHEK")
    print(f"{'─'*65}")
    print(f"Hook-Patterns:         {len(HOOK_PATTERNS)}")
    print(f"Watchtime-Patterns:    {len(WATCHTIME_PATTERNS)}")
    print(f"Meme-Strukturen:       {len(MEME_STRUCTURES)}")
    print(f"Transformations-Trends: {len(TRANSFORMATION_TRENDS)}")
    print(f"Plattform-Trends:      {len(PLATFORM_TRENDS)}")

    print(f"\n{'─'*65}")
    print("TREND-KONTEXT (Instagram)")
    ctx = agent.get_trend_context("instagram", randomize=True)
    print(f"Top-Hooks:        {[h.name for h in ctx.top_hooks]}")
    print(f"Top-Watchtime:    {[w.name for w in ctx.top_watchtime_patterns]}")
    print(f"Top-Memes:        {[m.name for m in ctx.top_meme_structures]}")
    print(f"Top-Transforms:   {[t.name for t in ctx.top_transformations]}")
    print(f"\nPrompt-Addendum (erste 200 Zeichen):")
    print(ctx.to_prompt_addendum()[:200] + "...")

    print(f"\n{'─'*65}")
    print("HOOK-ANALYSE")
    good_hook = "Eines Tages werden sie nicht mehr malen. Heute tun sie es noch."
    bad_hook = "In diesem Video zeige ich euch heute unsere tollen günstigen Rabattangebote!"
    for hook in [good_hook, bad_hook]:
        result = agent.analyze_hook_strength(hook, platform="instagram")
        print(f"\nHook: \"{hook[:50]}...\"" if len(hook) > 50 else f"\nHook: \"{hook}\"")
        print(f"  Muster:    {result.detected_pattern or 'keins'}")
        print(f"  Virality:  {result.estimated_virality}/10")
        print(f"  Probleme:  {result.issues or 'keine'}")

    print(f"\n{'─'*65}")
    print("VIRALITÄTS-EINSCHÄTZUNG")
    assess = agent.assess_virality(
        hook="Mein Kind hat diese Zeichnung gemalt. Jetzt ist sie echt.",
        platform="instagram",
        content_type="reel",
        emotion="Staunen",
        transformation_input="Kinderzeichnung",
    )
    print(f"Score:           {assess.virality_score}/10")
    print(f"Meme-Match:      {assess.matching_meme_structure or 'keins'}")
    print(f"Transform-Match: {assess.matching_transformation or 'keins'}")
    print(f"Format-Empf.:    {assess.format_recommendation}")
    print(f"Begründung:      {assess.reasoning}")

    print(f"\n{'='*65}")
    print("ALLE SCHNELLTESTS BESTANDEN\n")
