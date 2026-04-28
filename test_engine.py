import unittest
import sys

sys.path.append("/home/wa/VTTAI2")
import engine_mechanics
import engine_receive

class TestEngine(unittest.TestCase):
    def test_engine_mechanics_functions_exist(self):
        self.assertTrue(hasattr(engine_mechanics, "execute_action_mechanics"))
        self.assertTrue(hasattr(engine_mechanics, "roll_attack_only"))
        self.assertTrue(hasattr(engine_mechanics, "roll_damage_only"))

    def test_engine_receive_functions_exist(self):
        # We only check module-level exports
        self.assertTrue(hasattr(engine_receive, "build_patched_receive"))

    def test_execute_action_mechanics_missing_args(self):
        try:
            res = engine_mechanics.execute_action_mechanics(
                char_name="Tester", intention="Test", regle="Règle", cible="Cible",
                mj_note="MJ Note", single_attack=True, type_label="Action",
                char_mechanics={}, pending_smite={}, pending_skill_narrators={},
                app=None, extract_spell_name_fn=lambda x, y: None,
                is_spell_prepared_fn=lambda x, y: True,
                get_prepared_spell_names_fn=lambda x: []
            )
            self.assertTrue(isinstance(res, str))
        except Exception as e:
            self.fail(f"execute_action_mechanics raised an exception: {e}")

if __name__ == "__main__":
    unittest.main()
