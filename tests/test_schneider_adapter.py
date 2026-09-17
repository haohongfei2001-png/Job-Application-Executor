from pathlib import Path
from executor.adapters.schneider_boss import SchneiderBossAdapter

def fake_adapter():
    a=object.__new__(SchneiderBossAdapter)
    a.capture_data={
      'cities':[
        {'name':'中国','code':'001','subLevelModelList':[
          {'name':'山东省','code':'370000','subLevelModelList':[{'name':'临沂市','code':'371300','subLevelModelList':[{'name':'莒南县','code':'371327'}]}]},
          {'name':'天津市','code':'120000','subLevelModelList':[{'name':'南开区','code':'120104'}]},
          {'name':'上海市','code':'310000','subLevelModelList':[{'name':'普陀区','code':'310107'}]},
        ]}
      ],
      'resume':{'data':{'BASE_INFO':[{}],'CONTACT':[{}],'ENGLISH':[{}],'ATTACHMENT':[{}]}},
      'template':{'rtFieldGroupList':[
        {'groupId':'BASE_INFO','groupName':'个人基本信息','fields':[
          {'id':'certificatePhoto','formType':'attachment','fileTypes':['PNG','JPG'],'maxSize':2,'required':True},
          {'id':'field-1721014054734','formType':'cascader','required':True},
          {'id':'field-1721014095457','formType':'cascader','required':True},
          {'id':'bossSalary','code':'bossSalary','formType':'other','required':True},
        ]},
        {'groupId':'CONTACT','groupName':'联系方式','fields':[]},
        {'groupId':'EDUCATION','groupName':'教育经历','fields':[
          {'id':'educationBackground','formType':'select'}, {'id':'schoolName','formType':'school'},
          {'id':'degree','formType':'select'}, {'id':'otherDegree','formType':'text','required':True},
          {'id':'major','formType':'select'}, {'id':'timeSlot','formType':'dateRange'},
          {'id':'field-1721014512580','formType':'select'}]},
        {'groupId':'ENGLISH','groupName':'语言','fields':[]},
        {'groupId':'ATTACHMENT','groupName':'其他','fields':[
          {'id':'bossAttachment','formType':'attachment','fileTypes':['PDF','PNG'],'maxSize':20,'required':True},
          {'id':'field-1721029480548','formType':'select','required':True,'options':[{'label':'是','value':'yes1'},{'label':'否','value':'no1'}]},
        ]},
      ]},
    }
    return a

def app_profile():
    return {'full_name':'Test','gender':'男','birthday_month':'2001-12','id_card':'X','phone':'1','email':'x@example.com',
      'resident_city_path':['中国','山东省','临沂市','莒南县'],'living_city_path':['中国','天津市','南开区'],
      'graduation_month':'2027-06','education':[{'education_code':'204','school':'南开大学','degree_code':'1','major':'理论物理','start_month':'2024-09','end_month':'2027-06'}]}

def test_schneider_dates_and_decisions_use_server_formats():
    a=fake_adapter(); d=a.build_safe_data(app_profile())
    assert d['BASE_INFO'][0]['birthday']=='2001/12'
    assert d['BASE_INFO'][0]['highestDegreeGraduationDate']=='2027/06'
    assert d['EDUCATION'][0]['timeSlot']==['2024/09','2027/06']
    a.apply_decisions(d,{'work_city_path':['中国','上海市','普陀区'],'interview_city_path':['中国','上海市','普陀区'],'salary':'negotiable','compliance':{'field-1721029480548':False}})
    assert d['BASE_INFO'][0]['field-1721014054734']==['001','310000','310107']
    assert d['BASE_INFO'][0]['bossSalary']==[0,0]
    assert d['ATTACHMENT'][0]['field-1721029480548']==['no1']

def test_schneider_asset_validation(tmp_path):
    a=fake_adapter(); pdf=tmp_path/'resume.pdf'; pdf.write_bytes(b'%PDF-1.4\n')
    png=tmp_path/'photo.png'; png.write_bytes(b'x'*100)
    assert a.validate_asset('bossAttachment',pdf)['ext']=='PDF'
    assert a.validate_asset('certificatePhoto',png)['ext']=='PNG'

def test_conditional_other_degree_not_unresolved():
    a=fake_adapter(); d=a.build_safe_data(app_profile())
    names={x['id'] for x in a.unresolved_required(d)}
    assert 'otherDegree' not in names
