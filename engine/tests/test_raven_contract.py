import unittest

from server.raven_contract import (CATALOG_VERSION, TOOLS, TOOL_SCHEMAS, catalogue, expert_system_prompt,
                                   parse_model_reply, validate_actions, validate_request)


class RavenContractTests(unittest.TestCase):
    def test_editorial_inspection_is_project_bound_read_only_and_strict(self):
        tools = ('inspect_cut', 'inspect_coverage', 'inspect_structure', 'inspect_sound')
        for tool in tools:
            entry = next(t for t in catalogue() if t['name'] == tool)
            self.assertTrue(entry['requiresProject'])
            self.assertFalse(entry['requiresApproval'])
            self.assertTrue(validate_actions([{'tool': tool}], has_project=False)[1])
            self.assertTrue(validate_actions([{'tool': tool, 'path': '/outside'}], slug='demo', has_project=True)[1])
        actions = [{'tool': tool} for tool in tools]
        self.assertEqual(validate_actions(actions, slug='demo', has_project=True), (actions, []))
        for panel in ('scenes', 'cut', 'coverage', 'structure', 'sound'):
            action = [{'tool': 'open_panel', 'panel': panel}]
            self.assertEqual(validate_actions(action, slug='demo', has_project=True), (action, []))
            self.assertTrue(validate_actions(action, has_project=False)[1])

    def test_catalog_and_no_project_request(self):
        self.assertEqual(validate_request({"slug": None, "message": "start"}, set())[1], [])
        names = {item["name"] for item in TOOLS}
        self.assertIn("create_project", names)
        self.assertTrue(next(t for t in catalogue() if t["name"]=="create_project")["requiresApproval"])
        self.assertFalse(next(t for t in catalogue() if t["name"]=="list_seed_takes")["requiresApproval"])
        self.assertIn("generate_image", names)
        self.assertEqual(TOOL_SCHEMAS["prepare_film"]["required"], ["revision"])
        self.assertEqual(TOOL_SCHEMAS["stop_film"]["required"], ["jobId"])
        self.assertIn("schema", next(x for x in catalogue() if x["name"] == "set_shot_anchors"))

    def test_proposals_are_bounded(self):
        actions, errors = validate_actions(
            [{"tool": "add_shot", "title": str(i)} for i in range(7)],
            slug="demo", known_slugs={"demo"}, has_project=True)
        self.assertTrue(errors)
        self.assertEqual(actions, [])
        _, errors = validate_actions([{"tool": "approve_image_plan"}], slug="demo",
                                     known_slugs={"demo"}, has_project=True)
        self.assertTrue(errors)

    def test_revision_and_anchor_shapes(self):
        c = {"shots": ["s1"], "images": ["project:i"], "sharedLibrary": []}
        good, errors = validate_actions([{"tool": "set_shot_anchors", "shotId": "s1", "anchorRevision": "r1", "anchors": [{"imageId": "project:i", "frame": "last", "strength": 0.5}]}], c, slug="demo", has_project=True)
        self.assertFalse(errors); self.assertEqual(len(good), 1)
        _, errors = validate_actions([{"tool": "set_film_anchor", "enabled": True}], c, slug="demo", has_project=True)
        self.assertTrue(errors)

    def test_prompt_and_malformed_reply(self):
        prompt = expert_system_prompt()
        self.assertIn(CATALOG_VERSION, prompt)
        self.assertIn("Qwen-Image-2.1-Turbo", prompt)
        parsed, errors = parse_model_reply("not json")
        self.assertEqual(parsed["proposalStatus"], "incomplete")
        self.assertTrue(errors)

    def test_known_wrapper_is_repaired_but_conflicts_are_not(self):
        wrapped = {"tool": "create_project", "arguments": {"name": "Maur"}}
        good, errors = validate_actions([wrapped], has_project=False)
        self.assertFalse(errors)
        self.assertEqual(good, [{"tool": "create_project", "name": "Maur"}])
        _, errors = validate_actions([{"tool": "create_project", "args": {"name": "Maur"}, "name": "other"}], has_project=False)
        self.assertTrue(errors)
        _, errors = validate_actions([{"tool": "create_project", "arguments": {"name": "/tmp/x"}}], has_project=False)
        self.assertTrue(errors)
        _, errors = validate_actions([{"tool": "made_up", "arguments": {"name": "x"}}], has_project=False)
        self.assertTrue(errors)

    def test_only_create_then_manuscript_is_virtual_chain(self):
        actions, errors = validate_actions([
            {"tool": "create_project", "name": "Maursoldaten", "newProject": True},
            {"tool": "write_manuscript", "brief": "En ensom soldat finner hjem", "count": 12},
        ], has_project=False)
        self.assertFalse(errors)
        self.assertEqual([a["tool"] for a in actions], ["create_project", "write_manuscript"])
        _, errors = validate_actions([
            {"tool": "create_project", "name": "Maursoldaten"},
            {"tool": "add_character", "name": "Soldaten"},
        ], has_project=False)
        self.assertTrue(errors)

    def test_stage_catalogue_is_narrow(self):
        self.assertEqual({x["name"] for x in catalogue("shots")}, {"add_shot"})
        self.assertEqual({x["name"] for x in catalogue("setup")}, {"edit_project", "edit_bible", "add_character"})
        _, errors = validate_actions([{"tool": "generate_image", "prompt": "x"}], has_project=True, stage="shots")
        self.assertTrue(errors)

    def test_wrapped_tool_metadata_and_reserved_fields_are_rejected(self):
        _, errors = validate_actions([{"tool": {"name": "status", "arguments": {}, "approve": True}}], has_project=True)
        self.assertTrue(errors)
        _, errors = validate_actions([{"tool": "status", "arguments": {"tool": "status"}}], has_project=True)
        self.assertTrue(errors)

    def test_existing_authoring_batches_remain_bounded_and_valid(self):
        setup, errors = validate_actions([
            {"tool": "edit_project", "fields": {"sceneDescription": "A lost soldier"}},
            {"tool": "add_character", "name": "The soldier"},
        ], slug="demo", known_slugs={"demo"}, has_project=True, stage="setup")
        self.assertFalse(errors)
        self.assertEqual(len(setup), 2)
        shots, errors = validate_actions([
            {"tool": "add_shot", "title": f"Shot {i}", "prompt": "English start, action, end, camera"}
            for i in range(6)
        ], slug="demo", known_slugs={"demo"}, has_project=True, stage="shots")
        self.assertFalse(errors)
        self.assertEqual(len(shots), 6)


if __name__ == "__main__":
    unittest.main()
