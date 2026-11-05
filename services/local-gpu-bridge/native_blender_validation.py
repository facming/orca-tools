"""Shared typed Blender arguments; process and path policy belong to each platform."""
import math,pathlib

BLENDER_FIELDS={
 'scene_info':set(),'open':{'path'},'save':{'path','replace'},'import_asset':{'path'},'export_asset':{'path','replace'},
 'create_primitive':{'kind','name','location','rotation','scale'},'set_transform':{'name','location','rotation','scale'},
 'set_material':{'name','color','roughness','metallic'},'set_camera':{'location','target','lens'},
 'delete_object':{'name'},'render':{'engine','width','height','samples'}
 ,'execute_code':{'code','parameters','timeout_seconds','output_paths'},'run_script':{'path','user_args','parameters','timeout_seconds','output_paths'},
 'viewport_capture':{'width','height'},'batch':{'steps','continue_on_error','timeout_seconds'}
}

def validate_blender(operation,args,root,inside,allow_missing=False):
    fields=BLENDER_FIELDS.get(operation)
    if fields is None or set(args)-fields:raise ValueError('Blender operation or argument is not exposed.')
    if operation=='batch':
        steps=args.get('steps')
        if not isinstance(steps,list) or not 1<=len(steps)<=32:raise ValueError('Provide 1 to 32 batch steps.')
        if 'continue_on_error' in args and type(args['continue_on_error'])is not bool:raise ValueError('continue_on_error must be boolean.')
        for step in steps:
            if not isinstance(step,dict) or set(step)-{'operation','arguments'} or step.get('operation')=='batch' or not isinstance(step.get('arguments',{}),dict):raise ValueError('Invalid batch step; nested batches are not supported.')
            validate_blender(step.get('operation'),step.get('arguments',{}),root,inside,allow_missing)
    if 'output_paths' in args:
        paths=args['output_paths']
        if not isinstance(paths,list) or len(paths)>32:raise ValueError('Provide at most 32 output paths.')
        for name in paths:inside(root,name)
    required={'open':{'path'},'save':{'path'},'import_asset':{'path'},'export_asset':{'path'},'create_primitive':{'kind'},'set_transform':{'name'},'set_material':{'name','color'},'set_camera':{'location'},'delete_object':{'name'},'execute_code':{'code'},'run_script':{'path'}}.get(operation,set())
    if not required.issubset(args):raise ValueError('Required Blender fields are missing.')
    if 'path' in args:inside(root,args['path'])
    if operation=='run_script':
        file=inside(root,args['path'])
        if file.suffix!='.py' or (not file.is_file() and not allow_missing) or file.is_dir():raise ValueError('Select an existing project .py script.')
        from native_script import arguments
        arguments(args.get('user_args',[]))
    if operation=='execute_code' and (not isinstance(args['code'],str) or not 1<=len(args['code'])<=256000):raise ValueError('Provide Python code of at most 256 KB.')
    if 'parameters' in args and not isinstance(args['parameters'],dict):raise ValueError('parameters must be an object.')
    if 'timeout_seconds' in args and (type(args['timeout_seconds'])is not int or not 10<=args['timeout_seconds']<=7200):raise ValueError('Script timeout must be 10 to 7200 seconds.')
    if operation in ('open','save') and pathlib.PurePosixPath(args['path']).suffix.lower()!='.blend':raise ValueError('Select a .blend file.')
    for key in ('name',):
        if key in args and (not isinstance(args[key],str) or not 1<=len(args[key])<=120):raise ValueError('Invalid object name.')
    for key in ('location','rotation','scale','target'):
        if key in args and (not isinstance(args[key],list) or len(args[key])!=3 or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e6 for v in args[key])):raise ValueError('Invalid transform vector.')
    for key,default,minimum,maximum in [('width',1280,64,4096),('height',720,64,4096),('samples',32,1,512)]:
        if key in args and (type(args[key])is not int or not minimum<=args[key]<=maximum):raise ValueError('Invalid render dimensions or sample count.')
    if operation=='render' and args.get('engine','cycles') not in ('cycles','eevee'):raise ValueError('Only GPU render engines are exposed.')
    if 'lens' in args and (type(args['lens'])not in (int,float) or not 1<=args['lens']<=500):raise ValueError('Invalid camera lens.')
    if 'replace' in args and type(args['replace'])is not bool:raise ValueError('replace must be boolean.')
