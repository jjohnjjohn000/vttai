#!/usr/bin/env python3
"""
test_class_data.py — Tests unitaires pour le chargeur de données de classes D&D 5e.

Valide que les données lues depuis class/*.json correspondent aux valeurs
officielles du Player's Handbook.

Usage :
    cd /home/wa/VTTAI2 && python -m pytest test_class_data.py -v
"""

import pytest
from class_data import (
    get_hit_die,
    get_spell_slots,
    get_caster_progression,
    get_class_features,
    get_subclass_features,
    get_proficiencies,
    get_subclass_spells,
    get_combat_prompt,
)


# ── Hit Die ──────────────────────────────────────────────────────────────────

class TestHitDie:
    def test_paladin(self):
        assert get_hit_die("paladin") == 10

    def test_wizard(self):
        assert get_hit_die("wizard") == 6

    def test_cleric(self):
        assert get_hit_die("cleric") == 8

    def test_rogue(self):
        assert get_hit_die("rogue") == 8

    def test_fighter(self):
        assert get_hit_die("fighter") == 10

    def test_barbarian(self):
        assert get_hit_die("barbarian") == 12

    def test_case_insensitive(self):
        assert get_hit_die("Paladin") == 10
        assert get_hit_die("WIZARD") == 6


# ── Spell Slots ──────────────────────────────────────────────────────────────

class TestSpellSlots:
    def test_paladin_level_15(self):
        # Paladin L15 : 4/3/3/2  (demi-lanceur — 5e slot à L17 seulement)
        slots = get_spell_slots("paladin", 15)
        assert slots == {"1": 4, "2": 3, "3": 3, "4": 2}

    def test_paladin_level_1(self):
        # Paladin L1 : pas encore de sorts (commence à L2)
        slots = get_spell_slots("paladin", 1)
        assert slots == {}

    def test_paladin_level_2(self):
        # Paladin L2 : 2 emplacements de niv 1
        slots = get_spell_slots("paladin", 2)
        assert slots == {"1": 2}

    def test_wizard_level_15(self):
        # Wizard L15 : 4/3/3/3/2/1/1/1  (lanceur complet)
        slots = get_spell_slots("wizard", 15)
        assert slots == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2, "6": 1, "7": 1, "8": 1}

    def test_cleric_level_15(self):
        # Cleric L15 : identique au Wizard (lanceur complet)
        slots = get_spell_slots("cleric", 15)
        assert slots == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2, "6": 1, "7": 1, "8": 1}

    def test_rogue_no_spell_slots(self):
        # Rogue base (non Arcane Trickster) : pas de sorts
        slots = get_spell_slots("rogue", 15)
        assert slots == {}

    def test_fighter_no_spell_slots(self):
        slots = get_spell_slots("fighter", 15)
        assert slots == {}


# ── Caster Progression ───────────────────────────────────────────────────────

class TestCasterProgression:
    def test_paladin_half(self):
        assert get_caster_progression("paladin") == "1/2"

    def test_wizard_full(self):
        assert get_caster_progression("wizard") == "full"

    def test_cleric_full(self):
        assert get_caster_progression("cleric") == "full"

    def test_rogue_none(self):
        assert get_caster_progression("rogue") is None

    def test_fighter_none(self):
        assert get_caster_progression("fighter") is None


# ── Class Features ───────────────────────────────────────────────────────────

class TestClassFeatures:
    def test_paladin_features_level_15(self):
        feats = get_class_features("paladin", 15)
        assert "Divine Sense" in feats
        assert "Lay on Hands" in feats
        assert "Divine Smite" in feats
        assert "Extra Attack" in feats
        assert "Aura of Protection" in feats
        assert "Aura of Courage" in feats
        assert "Improved Divine Smite" in feats
        assert "Cleansing Touch" in feats

    def test_rogue_features_level_15(self):
        feats = get_class_features("rogue", 15)
        assert "Sneak Attack" in feats
        assert "Cunning Action" in feats
        assert "Uncanny Dodge" in feats
        assert "Evasion" in feats
        assert "Reliable Talent" in feats
        assert "Slippery Mind" in feats

    def test_wizard_features(self):
        feats = get_class_features("wizard", 15)
        assert "Arcane Recovery" in feats
        assert "Spellcasting" in feats

    def test_excludes_asi(self):
        feats = get_class_features("paladin", 20)
        assert not any("Ability Score Improvement" in f for f in feats)


# ── Subclass Features ────────────────────────────────────────────────────────

class TestSubclassFeatures:
    def test_devotion_paladin(self):
        feats = get_subclass_features("paladin", "Devotion", 15)
        assert "Oath of Devotion" in feats
        assert "Aura of Devotion" in feats
        assert "Purity of Spirit" in feats

    def test_assassin_rogue(self):
        feats = get_subclass_features("rogue", "Assassin", 15)
        assert "Assassin" in feats
        assert "Infiltration Expertise" in feats
        assert "Impostor" in feats

    def test_life_cleric(self):
        feats = get_subclass_features("cleric", "Life", 15)
        assert "Life Domain" in feats
        assert "Blessed Healer" in feats

    def test_unknown_subclass(self):
        feats = get_subclass_features("paladin", "NONEXISTENT", 15)
        assert feats == []


# ── Subclass Spells ──────────────────────────────────────────────────────────

class TestSubclassSpells:
    def test_devotion_paladin_spells(self):
        spells = get_subclass_spells("paladin", "Devotion", 15)
        assert len(spells) > 0
        # Devotion L3 spells
        assert "Protection From Evil And Good" in spells or "protection from evil and good" in [s.lower() for s in spells]
        assert "Sanctuary" in spells or "sanctuary" in [s.lower() for s in spells]

    def test_life_cleric_spells(self):
        spells = get_subclass_spells("cleric", "Life", 15)
        assert len(spells) > 0
        lower_spells = [s.lower() for s in spells]
        assert "bless" in lower_spells
        assert "cure wounds" in lower_spells

    def test_assassin_no_domain_spells(self):
        spells = get_subclass_spells("rogue", "Assassin", 15)
        assert len(spells) == 0


# ── Proficiencies ────────────────────────────────────────────────────────────

class TestProficiencies:
    def test_paladin_armor(self):
        profs = get_proficiencies("paladin")
        assert "heavy" in profs["armor"]
        assert "shield" in profs["armor"]

    def test_paladin_weapons(self):
        profs = get_proficiencies("paladin")
        assert "simple" in profs["weapons"]
        assert "martial" in profs["weapons"]

    def test_paladin_saves(self):
        profs = get_proficiencies("paladin")
        assert "wis" in profs["saves"]
        assert "cha" in profs["saves"]

    def test_wizard_armor(self):
        profs = get_proficiencies("wizard")
        assert profs["armor"] == []


# ── Combat Prompt ────────────────────────────────────────────────────────────

class TestCombatPrompt:
    def test_not_empty(self):
        prompt = get_combat_prompt("paladin", "Devotion", 15)
        assert len(prompt) > 50

    def test_contains_class_name(self):
        prompt = get_combat_prompt("paladin", "Devotion", 15)
        assert "Paladin" in prompt

    def test_contains_hit_die(self):
        prompt = get_combat_prompt("wizard", "", 15)
        assert "d6" in prompt

    def test_contains_spell_slots(self):
        prompt = get_combat_prompt("cleric", "Life", 15)
        assert "Lanceur de sorts" in prompt
        # Should contain slot values
        assert "4/3/3/3/2/1/1/1" in prompt

    def test_rogue_no_caster(self):
        prompt = get_combat_prompt("rogue", "Assassin", 15)
        assert "Lanceur de sorts" not in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
