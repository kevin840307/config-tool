CAPABILITIES={
'operations':['set','replace','remove','merge','rename_key','insert_key','append','prepend','insert','insert_at','insert_before','insert_after','update_item','upsert_item','remove_item','copy_item','move_item','copy_node','move_node','copy_key','move_key','copy_item_to_node','capture'],
'commands':['apply','compile','verify','compile-folder','apply-folder','apply-rules-folder','plan-rules-folder','check-idempotency','verify-folder','validate-config','lint','run-folder']}

def report():
    return {'version':1,'yaml':CAPABILITIES,'xml':CAPABILITIES,'aligned':True}
