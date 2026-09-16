from executor.field_classifier import classify,is_final_submit,is_next,is_initial_apply

def test_basic_fields():
    assert classify('邮箱','email','')[1]=='identity.email'
    assert classify('Phone Number','tel','tel')[1]=='identity.phone'
    assert classify('毕业时间','text','')[1]=='education.graduation_date'
    assert classify('Upload Resume','file','')[0]=='AUTO_UPLOAD'

def test_manual_decisions():
    assert classify('Expected salary','text','')[0]=='ASK_USER'
    assert classify('Do you require visa sponsorship?','text','')[0]=='ASK_USER'
    assert classify('Gender','select','')[0]=='ASK_USER'

def test_submit_barrier():
    for x in ['Submit','Submit application','提交申请','立即投递']: assert is_final_submit(x)
    assert not is_final_submit('Save and Continue')
    assert is_next('Next') and is_next('保存并继续')
    assert is_initial_apply('Apply now') and not is_final_submit('Apply now')
