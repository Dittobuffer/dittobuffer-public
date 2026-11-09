"""Replacement authorization cannot create species, forms or incomplete owners."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('kanto_replacements', Path(__file__).resolve().parents[1] / 'scripts/m113-replacement-catalog.py')
replacement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replacement)
publish_spec = importlib.util.spec_from_file_location('kanto_publication', Path(__file__).resolve().parents[1] / 'scripts/m113-publish-replacements.py')
publication = importlib.util.module_from_spec(publish_spec)
publish_spec.loader.exec_module(publication)


class KantoReplacementSafetyTests(unittest.TestCase):
    def inventory(self, **changes):
        candidate = dict(address='pm49', dependency_complete=True, exact_current_owner=False)
        candidate.update(changes)
        return dict(records=[dict(address='pm49', candidate=candidate)])

    def test_explicit_existing_same_appearance_candidate_is_allowed(self):
        self.assertEqual(set(replacement.validate_keys(['pm49'], self.inventory())), {'pm49'})

    def test_new_species_max_and_unknown_keys_cannot_enter_replacement(self):
        for key in ['pm152', 'pm49.fMEGA', 'pm49.dMax', 'pm49.fLOAF', 'pm49.fETERNAMAX']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                replacement.validate_keys([key], self.inventory())

    def test_name_matching_does_not_authorize_a_different_appearance(self):
        for address in ['pm48', 'pm49.s', 'pm49.fALOLA']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                replacement.validate_keys(['pm49'], self.inventory(address=address))

    def test_incomplete_unchanged_or_duplicate_candidates_are_rejected(self):
        for candidate in [self.inventory(dependency_complete=False), self.inventory(exact_current_owner=True)]:
            with self.assertRaises(ValueError):
                replacement.validate_keys(['pm49'], candidate)
        with self.assertRaises(ValueError):
            replacement.validate_keys(['pm49', 'pm49'], self.inventory())

    def test_publication_requires_comparison_and_two_real_processes(self):
        changed = dict(before_owner_sha256='before', after_owner_sha256='original')
        review = dict(rendered=True, restart_rendered=True, accepted_prefab_sha256='original', reviewed_evidence=['warm.json', 'cold.json'])
        comparison = dict(accepted_original=True, before_owner_sha256='before', after_owner_sha256='original', reviewed_images={'comparison.png': 'hash'}, assessment='Exact original eye materials reviewed')
        proofs = [dict(key='pm49', owner='original', native_passed=True, role=role, pid=pid, file=file) for role, pid, file in [('warm', 1, 'warm.json'), ('cold', 2, 'cold.json')]]
        self.assertTrue(publication.approved('pm49', changed, review, comparison, proofs))
        self.assertFalse(publication.approved('pm49', changed, review, {}, proofs))
        self.assertFalse(publication.approved('pm49', changed, review, comparison, [dict(r, pid=1) for r in proofs]))
        self.assertFalse(publication.approved('pm49', changed, review, dict(comparison, after_owner_sha256='unreviewed'), proofs))


if __name__ == '__main__':
    unittest.main()
