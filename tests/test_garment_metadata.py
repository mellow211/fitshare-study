import base64
import json
import unittest
from tests import test_garment_registration as fixtures
from tools.garment_metadata import clean_metadata


class GarmentMetadataApiTests(unittest.TestCase):
    setUp = fixtures.GarmentRegistrationTests.setUp
    tearDown = fixtures.GarmentRegistrationTests.tearDown
    request_json = fixtures.GarmentRegistrationTests.request_json
    post_error = fixtures.GarmentRegistrationTests.post_error

    def payload(self):
        return dict(name='봄 가을 긴팔', category='top', garmentTypeId=1, gender='unisex', lengthCm=50, widthCm=40, colorName='파랑',
                    photoDataUrl='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xffsample').decode(),
                    size='140',seasons=['봄','가을'],style='단정한 스타일',condition='보통',garmentKind='티셔츠',sleeveLength='긴팔',bottomLength='해당 없음',
                    thickness='보통',stretch='약간',pattern='줄무늬',measurementMethod='manual',metadataSources={'sleeveLength':'name-rule','size':'manual'})

    def signup(self):
        self.request_json('/api/auth/signup','POST',{'username':'meta-user','name':'Metadata user','password':'test-pw'})

    def test_save_detail_list_keep_all_metadata(self):
        self.signup()
        payload=self.payload()
        _,saved=self.request_json('/api/garments','POST',payload)
        _,detail=self.request_json('/api/garments/'+saved['id'])
        _,items=self.request_json('/api/garments')
        for key in ('size','seasons','style','condition','garmentKind','sleeveLength','bottomLength','thickness','stretch','pattern'):
            self.assertEqual(detail[key],payload[key]);self.assertEqual(items[0][key],payload[key])
        self.assertEqual(detail['ownerName'],'Metadata user')
        self.assertEqual(detail['metadataSources']['sleeveLength'],'name-rule')
        self.assertEqual(detail['measurementMethod'],'manual')

    def test_partial_edit_preserves_metadata_owner_and_photo(self):
        self.signup()
        _,saved=self.request_json('/api/garments','POST',self.payload())
        _,updated=self.request_json('/api/garments/'+saved['id']+'/update','POST',{'style':'활동적인 스타일','seasons':['여름']})
        self.assertEqual(updated['size'],'140');self.assertEqual(updated['thickness'],'보통')
        self.assertEqual(updated['style'],'활동적인 스타일');self.assertEqual(updated['season'],'여름')
        self.assertEqual(updated['ownerName'],saved['ownerName'])
        self.assertEqual(updated['photoUrl'],saved['photoUrl'])
        self.assertEqual(updated['metadataSources']['sleeveLength'],'name-rule')
        self.assertEqual(updated['metadataSources']['style'],'manual')

    def test_invalid_metadata_rejected_before_writing(self):
        for invalid in ({'size':'999'},{'size':140},{'seasons':['봄','봄']},{'seasons':['장마']},{'seasons':{}},{'stretch':True},{'condition':'자동판단'},{'metadataSources':{'size':[]}},{'bottomLength':'긴바지'},{'garmentKind':'바지'},{'measurementMethod':'guessed'}):
            with self.subTest(invalid=invalid):self.post_error('/api/garments',{**self.payload(),**invalid},400)
        _,records=self.request_json('/api/garments');self.assertEqual(records,[])

    def test_invalid_edit_does_not_change_record(self):
        self.signup()
        _,saved=self.request_json('/api/garments','POST',self.payload())
        self.post_error('/api/garments/'+saved['id']+'/update',{'size':'invalid','condition':'좋음'},400)
        _,detail=self.request_json('/api/garments/'+saved['id']);self.assertEqual(detail['size'],'140');self.assertEqual(detail['condition'],'보통')

    def test_unknown_optional_metadata_is_preserved_as_unknown(self):
        result=clean_metadata({'size':'','seasons':[],'condition':'','stretch':''})
        self.assertEqual(result['size'],'');self.assertEqual(result['seasons'],[]);self.assertEqual(result['condition'],'')
        self.assertNotIn('thickness',result)

    def test_measurement_rule_is_valid_provenance_for_estimated_size(self):
        result=clean_metadata({'size':'150','metadataSources':{'size':'measurement-rule'}})
        self.assertEqual(result['size'],'150')
        self.assertEqual(result['metadataSources']['size'],'measurement-rule')

    def test_type_change_does_not_keep_incompatible_old_details(self):
        result=clean_metadata({'category':'pants'},{'category':'top','garmentKind':'티셔츠','sleeveLength':'긴팔','bottomLength':'해당 없음'})
        self.assertEqual(result['garmentKind'],'바지');self.assertEqual(result['sleeveLength'],'해당 없음');self.assertEqual(result['bottomLength'],'')
        self.assertEqual(result['metadataSources']['garmentKind'],'type-rule')
        self.signup()
        _,saved=self.request_json('/api/garments','POST',self.payload())
        _,updated=self.request_json('/api/garments/'+saved['id']+'/update','POST',{'category':'pants','garmentTypeId':5})
        self.assertEqual(updated['garmentKind'],'바지');self.assertEqual(updated['sleeveLength'],'해당 없음');self.assertEqual(updated['bottomLength'],'')


if __name__=='__main__':unittest.main()
