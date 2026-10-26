import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.dat'
        self.source.write_bytes(b'explicitly synthetic test fixture, not a user video')
        shot = {key: 'not applicable' for key in w.FIELDS}
        shot.update(shot_id='SHOT_01',start_sec=0,end_sec=1,duration_sec=1,ref_frame_timestamp_sec=.5,
                    dialogue_vo_zh='测试内容',rewrite_vo_zh_translation='测试内容',reference_ids=['SHOT_01_A'],product_visible=True,
                    prompt_en=w.PRODUCT_LOCK+'; '+w.PACKAGE_LOCK)
        self.job = dict(source=str(self.source),source_sha256=w.digest(self.source),duration_sec=1,shots=[shot],
                        references=[dict(id='SHOT_01_A',shot_id='SHOT_01',source_timestamp_sec=.5,source_path='frames/a.png',edited_path='images/a.png',generation_status='planned_not_generated')],
                        groups=[dict(id='G01',requested_duration=15,reference_duration_sec=1,image_paths=['images/a.png'])],
                        h3=dict(verified_prices=dict(source_url='https://example.invalid/test-only-prices',checked_at='test-fixture',output_per_sec=.1,reference_video_per_sec=.1,free_images=5,extra_image=.05)))

    def approval(self):
        return dict(source_sha256=self.job['source_sha256'],user_quote='TEST ONLY: explicitly authorize this fixture',upload_approved=True,
                    paid_generation_approved=True,allowed_groups=['G01'],provider_host='test.invalid',attempts_per_group=1,accepted_estimated_total=2,budget_ceiling=2)

    def test_complete_timeline_and_43_columns(self):
        self.assertEqual(len(w.FIELDS),43)
        self.assertTrue(w.validate(self.job,self.root)['structure_pass'])

    def test_timeline_gap_is_not_success(self):
        self.job['shots'][0]['start_sec']=.1
        self.assertFalse(w.validate(self.job,self.root)['structure_pass'])

    def test_planned_images_never_count_as_complete(self):
        self.assertFalse(w.validate(self.job,self.root,True)['structure_pass'])
        with self.assertRaises(ValueError):w.grids(self.job,self.root)

    def test_original_cannot_impersonate_replacement(self):
        r=self.job['references'][0]
        for key in ['source_path','edited_path']:
            path=self.root/r[key];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'same original')
        r['generation_status']='generated_reviewed'
        self.assertTrue(any('equals original' in e for e in w.validate(self.job,self.root,True)['errors']))

    def test_output_directory_escape(self):
        with self.assertRaises(ValueError):w.output_path(self.root,'../outside.txt')
        with self.assertRaises(ValueError):w.output_path(self.root,self.root.parent/'outside.txt')

    def test_approval_requires_same_source_and_provider(self):
        approval=self.approval()
        self.assertGreater(w.authorize(self.job,approval,'G01','test.invalid'),0)
        with self.assertRaises(ValueError):w.authorize(self.job,approval,'G01','another.invalid')
        with self.assertRaises(ValueError):w.authorize(self.job,approval,'G02','test.invalid')
        self.source.write_bytes(b'changed fixture')
        with self.assertRaises(ValueError):w.authorize(self.job,approval,'G01','test.invalid')

    def test_estimated_budget_overrun_stops(self):
        approval=self.approval();approval['budget_ceiling']=.01
        with self.assertRaises(ValueError):w.authorize(self.job,approval,'G01','test.invalid')

    def test_product_graph_has_no_mask_or_composite(self):
        c=dict(width=384,height=688,unet='test-only',clip='test-only',clip_type='flux3',vae='test-only',seed=1,steps=16,prefix='test-only')
        graph=w.flux_graph('test.png','test-only',c);w.add_flux_reference(graph,'test-product.png')
        self.assertEqual(graph['12']['inputs']['positive'],['17',0])
        self.assertFalse(any(any(x in n['class_type'].lower() for x in ['mask','composite','crop']) for n in graph.values()))

    def test_actual_grid_of_synthetic_images(self):
        from PIL import Image
        p=self.root/'images/a.png';p.parent.mkdir();Image.new('RGB',(64,96),'red').save(p)
        result=w.grids(self.job,self.root)
        self.assertTrue(Path(result['grids'][0]).is_file())
        self.assertFalse(result['reviewed'])

    def test_captions_reject_draft_timing(self):
        words=self.root/'draft.json'
        w.write(words,dict(timing_source='draft_evenly_spaced',words=[]))
        with self.assertRaises(ValueError):w.captions(self.job,self.root,words)

    def test_query_never_downloads_another_tasks_result(self):
        payload={'items':[{'id':'another-job','content':{'url':'https://test.invalid/another.mp4'}},{'id':'our-job','content':{'url':'https://test.invalid/ours.mp4'}}]}
        self.assertEqual(w.provider_video_url(w.provider_record(payload,'our-job')),'https://test.invalid/ours.mp4')
        self.assertEqual(w.provider_record(payload,'missing-job'),{})

    def test_provider_result_variants(self):
        for payload in [{'output_url':'https://test.invalid/a.mp4'}, {'video_url':{'url':'https://test.invalid/a.mp4'}}, {'content':{'url':'https://test.invalid/download'}}]:
            self.assertTrue(w.provider_video_url(payload).startswith('https://test.invalid/'))
        self.assertEqual(w.provider_video_url({'url':'https://test.invalid/account-page'}),'')
        self.assertEqual(w.provider_video_url({'video_url':'http://test.invalid/a.mp4'}),'')

    @unittest.skipUnless((os.getenv('FFMPEG') or shutil.which('ffmpeg')) and (os.getenv('FFPROBE') or shutil.which('ffprobe')), 'FFmpeg not available')
    def test_real_ffmpeg_on_explicit_synthetic_media(self):
        source=self.root/'synthetic.mp4'
        w.run([w.executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=64x96:r=10:d=1','-an','-c:v','libx264','-pix_fmt','yuv420p',source])
        result=w.init_job(source,self.root/'job','SYNTHETIC TEST ONLY',[])
        self.assertAlmostEqual(result['duration_sec'],1,places=1)
        job=w.read(self.root/'job/job.json');job['references']=self.job['references']
        w.extract(job,self.root/'job')
        self.assertTrue((self.root/'job/frames/a.png').is_file())

    @unittest.skipUnless((os.getenv('FFMPEG') or shutil.which('ffmpeg')) and (os.getenv('FFPROBE') or shutil.which('ffprobe')), 'FFmpeg not available')
    def test_synthetic_assembly_captions_comparison_and_paid_duplicate_guard(self):
        # Local generated colour/silence fixtures only. No person, no model, no paid API.
        from PIL import Image
        raw=self.root/'synthetic_generated.mp4'
        w.run([w.executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=red:s=64x96:r=10:d=1','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',raw])
        self.job['source']=str(raw);self.job['source_sha256']=w.digest(raw)
        group=self.job['groups'][0]
        group.update(source_start=0,source_end=1,output_path=raw.name,reference_video_path=raw.name,
                     resolution='768P',ratio='9:16',prompt='SYNTHETIC TEST ONLY')
        self.job['h3']['model']='TEST-ONLY';self.job['image_review_complete']=True
        ref=self.job['references'][0]
        for field,colour in [('source_path','blue'),('edited_path','red')]:
            path=self.root/ref[field];path.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(64,96),colour).save(path)
        ref['generation_status']='generated_reviewed'
        with patch.dict(os.environ,{'H3_ENDPOINT':'https://test.invalid/generate','H3_QUERY_ENDPOINT':'https://test.invalid/query','H3_API_KEY':'TEST-ONLY-NOT-A-REAL-SECRET'}):
            with patch.object(w,'http_json',side_effect=AssertionError('No network allowed in local tests')):
                self.assertTrue(w.h3(self.job,self.root,'G01')['dry_run'])
                approval=self.root/'approval.json';w.write(approval,self.approval())
                w.write(self.root/'records/h3_G01.json',{'status':'attempt_reserved'})
                with self.assertRaises(ValueError):w.h3(self.job,self.root,'G01',True,approval)
        result=w.assemble(self.job,self.root,width=64,height=96,fps=10)
        base=Path(result['video']);self.assertTrue(base.is_file())
        self.job['subtitles']=dict(play_res_x=64,play_res_y=96,font_name='Arial',font_size=10,x=32,y=48,outline=0)
        self.job['subtitles_required']=True
        words=self.root/'synthetic_words.json'
        w.write(words,dict(fixture='SYNTHETIC local timing harness, no speech-generation claim',timing_source='manually_verified_actual_audio',text_reviewed_against_rewrite=True,video_sha256=w.digest(base),words=[dict(text='TEST ONLY',start=.1,end=.8)]))
        captioned=w.captions(self.job,self.root,words)
        self.assertTrue(Path(captioned['captioned_video']).is_file())
        compared=w.comparison(self.job,self.root)
        self.assertTrue(Path(compared['comparison']).is_file())


if __name__=='__main__':unittest.main()
