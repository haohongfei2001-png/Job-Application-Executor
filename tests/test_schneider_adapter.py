from executor.adapters.schneider_boss import SchneiderBossAdapter


def _adapter():
    a=SchneiderBossAdapter('https://xiaoyuan.zhipin.com/volunteer/index?encryptJobId=x')
    a.capture_data['template']={
        'rtFieldGroupList':[
            {'groupId':'BASE_INFO','fields':[{'id':'certificatePhoto','formType':'attachment'}]},
            {'groupId':'ATTACHMENT','fields':[{'id':'bossAttachment','formType':'attachment'},
                                               {'id':'conflict','formType':'select','options':[{'label':'是','value':'Y'},{'label':'否','value':'N'}]}]},
        ]
    }
    a.capture_data['cities']=[{'name':'中国','code':'001','subLevelModelList':[{'name':'上海市','code':'310000','subLevelModelList':[{'name':'普陀区','code':'310107','subLevelModelList':None}]}]}]
    return a


def test_attachment_serialization_uses_encrypt_id():
    a=_adapter()
    data={'BASE_INFO':[{'certificatePhoto':[{'encryptId':'photo-1','name':'p.png'}]}],
          'ATTACHMENT':[{'bossAttachment':[{'encryptId':'resume-1','name':'r.pdf'}]}]}
    out=a._serialize_data_for_save(data)
    assert out['BASE_INFO'][0]['certificatePhoto']==['photo-1']
    assert out['ATTACHMENT'][0]['bossAttachment']==['resume-1']
    assert isinstance(data['BASE_INFO'][0]['certificatePhoto'][0],dict)


def test_apply_decisions_ignores_unanswered_compliance():
    a=_adapter()
    data={'BASE_INFO':[{}],'ATTACHMENT':[{}]}
    a.apply_decisions(data,{'work_city_path':['中国','上海市','普陀区'],
                            'interview_city_path':['中国','上海市','普陀区'],
                            'salary':None,
                            'compliance':{'conflict':None}})
    assert data['BASE_INFO'][0]['field-1721014054734']==['001','310000','310107']
    assert data['BASE_INFO'][0]['field-1721014095457']==['001','310000','310107']
    assert 'conflict' not in data['ATTACHMENT'][0]
