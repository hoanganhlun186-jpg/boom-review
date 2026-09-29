import sys,json,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.block_source_bounds import freeze_source_mapping,owned_source_ranges

class MappingTests(unittest.TestCase):
    def test_recover_lookahead_and_shifted_tail(self):
        rows=[dict(block_id=1,source_block_ids=[1,2],original_start=0,original_end=9),
              dict(block_id=2,source_block_ids=[3,4],original_start=9,original_end=16)]
        blocks=[dict(block_id=1,source_block_ids=[1,2,3],original_start=0,original_end=12),
                dict(block_id=2,source_block_ids=[3,4],original_start=1,original_end=8)]
        freeze_source_mapping(blocks,rows)
        self.assertEqual(blocks[1]['source_ranges'],[[9,16]])
        blocks[1]['original_start']=0
        blocks[1]['original_end']=1
        self.assertEqual(owned_source_ranges(blocks[1],rows,[]),[(9,16)])
    def test_overlap_rejected_before_mutation(self):
        blocks=[dict(block_id=1,original_start=0,original_end=10),dict(block_id=2,original_start=9,original_end=12)]
        with self.assertRaises(ValueError):freeze_source_mapping(blocks,[])
        self.assertNotIn('source_ranges',blocks[0])

if __name__=='__main__':unittest.main()
