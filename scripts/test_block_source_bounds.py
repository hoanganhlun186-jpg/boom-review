from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.block_source_bounds import owned_source_ranges, bounded_candidates, extend_within_scene

class BlockBoundsTests(unittest.TestCase):
    def test_preview_trims_tail_of_referenced_render_rows(self):
        rows=[dict(block_id=16,source_block_ids=[121,122],original_start=139.68,original_end=150.24),
              dict(block_id=17,source_block_ids=[123,124],original_start=150.24,original_end=160.8),
              dict(block_id=18,source_block_ids=[136,137],original_start=160.8,original_end=163.136)]
        block=dict(block_id=7,source_block_ids=[16,17,18],original_start=139.68,original_end=155.136,
                   review_clip=dict(mode='source_video_timestamp',start=139.68,end=155.136,source_block_ids=[16,17,18]))
        self.assertEqual(owned_source_ranges(block,rows,[]),[(139.68,155.136)])

    def test_script_references_grouped_render_row_ids(self):
        rows=[dict(block_id=1,source_block_ids='1,2,3',original_start=0,original_end=10.56),
              dict(block_id=2,source_block_ids='23,24',original_start=10.56,original_end=20.64)]
        block=dict(block_id=1,source_block_ids=[1,2],original_start=0,original_end=20.64)
        self.assertEqual(owned_source_ranges(block,rows,[]),[(0,20.64)])

    def test_extend_cut_without_repeating_or_entering_next_block(self):
        scenes=[dict(start=0,end=15)]
        self.assertEqual(extend_within_scene([(0,10)],12,scenes,20,[(13,20)]),[(0,12)])
        self.assertEqual(extend_within_scene([(0,10)],14,scenes,20,[(13,20)]),[(0,13)])
        self.assertEqual(extend_within_scene([(0,10)],12,scenes,20,[(10,20)]),[(0,10)])

    def test_extension_stops_at_actual_scene_boundary(self):
        scenes=[dict(start=0,end=11),dict(start=11,end=20)]
        self.assertEqual(extend_within_scene([(0,10)],12,scenes,20),[(0,11)])
        self.assertEqual(extend_within_scene([(0,11)],12,scenes,20),[(0,11)])
        self.assertEqual(extend_within_scene([(0,10)],12,[],20),[(0,10)])

    def test_extension_respects_source_end_and_consumed_footage(self):
        scenes=[dict(start=0,end=20)]
        self.assertEqual(extend_within_scene([(0,10)],12,scenes,11),[(0,11)])
        self.assertEqual(extend_within_scene([(0,10)],12,scenes,20,used_ranges=[(11,14)]),[(0,11)])
    def test_editor_selection_survives_stale_coverage_fields(self):
        block=dict(block_id=2,source_block_ids=[7,8],original_start=54,original_end=89,
                   source_start=10.5,source_end=21,
                   review_clip=dict(start=10.5,end=21,mode='source_video_timestamp'))
        render=[dict(block_id=2,source_block_ids=[7,8],original_start=10.5,original_end=21)]
        self.assertEqual(owned_source_ranges(block,render,[]),[(10.5,21)])
    def test_grouped_editor_ids_are_not_raw_source_ids(self):
        groups=[dict(block_id=1,source_block_ids=[1,2,3],original_start=0,original_end=10),
                dict(block_id=3,source_block_ids=[14,15,16,17],original_start=21,original_end=31)]
        block=dict(block_id=3,source_block_ids=[14,15,16,17],original_start=21,original_end=31)
        self.assertEqual(owned_source_ranges(block,groups,[]),[(21,31)])
        with self.assertRaises(ValueError):
            owned_source_ranges(dict(block_id=1,source_block_ids=[3],original_start=0,original_end=50),
                                [dict(block_id=3,source_block_ids=[14],original_start=21,original_end=31)],[])
    def test_source_ids_keep_disjoint_ranges_without_neighbor(self):
        blocks=[dict(block_id=1,original_start=0,original_end=3),
                dict(block_id=2,original_start=3,original_end=9),
                dict(block_id=3,original_start=9,original_end=12)]
        ranges=owned_source_ranges(dict(source_block_ids=[1,3],original_start=0,original_end=12),blocks,[])
        self.assertEqual(ranges,[(0,3),(9,12)])
        with self.assertRaisesRegex(ValueError,'Rút gọn'):
            bounded_candidates(ranges,7)
        self.assertEqual(bounded_candidates(ranges,6),[(0,3,0),(9,12,0)])

    def test_final_timestamps_are_not_source(self):
        with self.assertRaises(ValueError):
            owned_source_ranges(dict(block_id=1,start_in_final_video=0,end_in_final_video=10),[],[])

    def test_direct_range_includes_zero_and_clips_source_ids(self):
        self.assertEqual(owned_source_ranges(dict(original_start=0,original_end=2),[],[]),[(0,2)])
        self.assertEqual(owned_source_ranges(dict(source_block_ids=[1],original_start=2,original_end=4),
                         [dict(block_id=1,original_start=0,original_end=10)],[]),[(2,4)])

    def test_used_footage_is_not_repeated(self):
        self.assertEqual(bounded_candidates([(0,10)],6,0,[(2,6)]),[(0,2,0),(6,10,0)])
        with self.assertRaises(ValueError):bounded_candidates([(0,10)],7,0,[(2,6)])

    def test_missing_mapping_stops_instead_of_using_adjacent_block(self):
        with self.assertRaises(ValueError):
            owned_source_ranges(dict(source_block_ids=[8]),[dict(block_id=9,original_start=0,original_end=10)],[])

if __name__=='__main__':unittest.main()
