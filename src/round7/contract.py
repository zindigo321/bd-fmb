"""Trace the engineering contract to the supplied official DOCX, not README."""
from pathlib import Path
import math,shutil
from src.round2.evidence import read,write,digest
from src.round6.report import phenomena_text


def prepare(ctx,cfg,preflight):
    out=Path(ctx['output']);base=out/'baseline';shutil.copytree(preflight,base)
    prior=Path(cfg['round7']['source_round6']);source=prior/'audit_report.md';original=source.read_text(encoding='utf-8')
    state=read(prior/'observed_phenomena.json');old='未观测到的同点多星、跨星同点重访、自然合并/重叠分支保持false。'
    if original.count(old)!=1:raise ValueError('Expected exact round6 erratum source not found')
    corrected=original.replace(old,'现象状态以结构化结果为准：\n\n'+phenomena_text(state))
    (base/'round6_corrected_report.md').write_text(corrected,encoding='utf-8')
    erratum=dict(status='PASS',source_path=str(source),source_sha256=digest(source.read_bytes()),source_run_id=cfg['round7']['source_round6_run_id'],
        corrected_copy=str(base/'round6_corrected_report.md'),corrected_sha256=digest(corrected.encode('utf-8')),
        source_unchanged=True,science_unchanged=True,stk_rerun_for_erratum=False,observed_states={k:v['observed'] for k,v in state.items()})
    write(base/'round6_erratum.json',erratum)
    (base/'round6_erratum.md').write_text('# 第六轮报告勘误\n\n原报告保持不变。仅纠正固定模板中的现象状态，未重跑STK，未更改科学数据、原代码快照或业务状态。\n\n'
        +f'原报告：`{source}`\n\nSHA-256：`{erratum["source_sha256"]}`\n\n'+phenomena_text(state),encoding='utf-8')
    document=read(base/'official_document_extract.json');rows=[]
    def row(id,definition,unit,loc,quote,implementation,difference,status):
        rows.append(dict(id=id,definition=definition,unit=unit,official_source=dict(path=document['source'],sha256=document['sha256'],locator=loc,short_quote=quote),
            implementation=implementation,difference_or_open_question=difference,status=status))
    supported='OFFICIAL_DOCUMENT_SUPPORTED';assumed='ENGINEERING_ASSUMPTION';unresolved='UNRESOLVED'
    row('epoch_window','统一2027-01-01至01-08，完整7天','s','body[12]/table rows11-12','2027年1月1日 00:00:00；2027年1月8日 00:00:00',dict(epoch=cfg['epoch_utcg'],window=[0,604800]),'历元日期已明确；时间系统另列',supported)
    row('time_system','时间标签采用UTCG','UTCG','body[12]/table rows11-12','2027年1月1日 00:00:00',cfg['time_system'],'表格未注明UTC/UTCG/TAI；示例图片是不同日期，不能替代表格',assumed)
    row('j2000_j2','J2000初值、J2传播、30秒步长','s','body[12]/table rows2-3,10','J2；30S；J2000',dict(frame=cfg['reference_frame'],step=cfg['propagation_step_s']),'与冻结模型一致；STK内部常数不由这些词唯一确定',supported)
    row('orbit_height_shape','500至700km、平均地球半径、标准圆轨道','km','body[12]/table rows4-6','500-700KM(地球半径按平均半径考虑)；标准圆轨道',dict(h=600,a=cfg['earth']['mean_radius_km']+600,e=0),'600km在范围内，平均半径数值另列；SSO为J2平均节点公式工程实现',supported)
    row('earth_constants','平均半径、J2参考半径、mu、J2、年长及椭球','km,s','body[12]/table row5','地球半径按平均半径考虑',cfg['earth'],'文档未给数值；继续冻结原常数，未声称长期真实SSO',assumed)
    row('surface','椭球零高，关闭地形','km','body[13]/paragraph','满足几何可见条件',cfg['diagnostic_surface'],'椭球、地形与大地高基准未指定',assumed)
    row('attitude_boresight','本体+Z指地心；+X运动前进，右手坐标系','deg','body[12]/table row9; body[15-17]','(+Z)轴（相机视轴）指向地心；+X轴为卫星运动的前进方向',cfg['camera'],'对地定向支持；惯性速度还是地固速度未明确',supported)
    row('along_direction_mount','NadirECIVel惯性速度投影沿迹、单位安装四元数','quaternion','body[16]/paragraph','+X轴为卫星运动的前进方向',dict(attitude=cfg['camera'],mapping='STK Rectangular command cross then along; HorizontalHalfAngle=along, VerticalHalfAngle=cross'),'文档未点名NadirECIVel及安装；原工程假设和STK读回保持',assumed)
    width=2*600*math.tan(math.radians(.81));specified=15*600/530
    row('camera_angle_definition','沿迹0.001622度，交叉迹0.81度','deg','body[49]/table rows6-7','载荷沿迹视场0.001622°；载荷交叉迹视场0.81°',dict(along_half_deg=.001622,cross_half_deg=.81,ground_width_flat_km=width,official_width_formula_km=specified),
        '原文未写全角或半角；半角对应平面幅宽约16.9657km，与公式16.9811km近似一致但不能据此确认；全角约减半。未发现明确的相反定义',unresolved)
    row('camera_width','幅宽15*h/530','km','body[12]/table row8','15×h轨道高度/530km',dict(h_km=600,width_formula_km=specified,screening_not_changed=True),'精确球面/椭球与四舍五入解释未给；不得为了幅宽差异悄改视场',unresolved)
    row('ideal_imaging','理想连续成像，忽略能源存储云雨等','none','body[12]/table row13; body[13]','理想连续成像；不考虑云、雨等气象条件',dict(sensor='Camera',geometry_only=True),'已保存/重开约束检查，主任务不加入额外工程限制',supported)
    row('global_grid','全球1度网格，有效区域由主办方统一确定','deg','body[49]/table row2; body[71-80]','1°*1°；永久无法被载荷几何覆盖的区域不纳入评价范围',dict(global_evaluation='NOT_EXECUTED'),'官方有效区域和极区裁剪边界数据未提供；不能自行构造正式分母',unresolved)
    row('regional_grid','长三角0.1度统一网格','deg','body[49]/table row3; body[117],body[133]','0.1°*0.1°；全部统一评价网格',dict(local_full_count=3264,components=[3248,2,14],evaluated_count=16,evaluated_components=[16,0,0],point_hash='860d8f4b2b9391adaf68740bf43c500bdcd074f4bd579971d864332a6eb89071'),
        '本地AT边界及边界点包含规则为工程来源，原文没有足够边界坐标可认证；16点不代表全区',unresolved)
    row('analysis_cadence','全球推荐10秒、区域分析1秒','s','body[49]/table rows4-5','全球覆盖分析步长推荐10S；长三角地区重访分析1S',dict(propagation_s=30,basic_screen_s=5,native_fine_max_s=.001),
        '本轮5秒候选筛查加1ms局部Access不等于官方统一1秒全网格评估。仅作同条件工程样本，官方流程适用性未确认',assumed)
    row('coverage_denominator','按被访问有效网格数/固定有效网格总数，重复不计','fraction','body[73-79]','有效评价网格总数；重复覆盖不重复统计',dict(local_denominator=16,weight='point_count'),'网格数权重有依据；全球有效分母仍待主办方提供，不使用面积权重替代',supported)
    row('coverage_thresholds','T90首次累计达到90%，T100首次有效区域100%','s','body[83-90]','全球累计覆盖率首次达到90%；全部至少完成一次几何覆盖',dict(local_thresholds=[.9,1.],unreached=None),'工程样本阈值不能命名为官方全球T90/T100',supported)
    row('unreached_scoring','不到90%两覆盖项均0；不到100%则100项0','score','body[177-188]','S90=0；S100=0',dict(no_official_score=True,unreached_time=None),'文档规定分数处理，未规定导出文件未达时间的空值表示',supported)
    row('per_point_union','同一点全部星访问先求并集，重叠/相接合并','s','body[125-131]','星座中全部卫星与重点区域评价点；连续两次有效访问',dict(merge_tolerance_s=0),'同点连续访问定义支持此解释，但重叠/相接算法未逐字规定；保留工程假设',assumed)
    row('revisit_gap','后次开始减前次结束','s','body[126-131]','ΔTi=ti+1start−tistop',dict(internal_gap='next_start - previous_stop'),'工程定义与公式一致，仅对样本有效内部gap计算',supported)
    row('gap_weighting','对全部有效间隔按数量M汇总均值，最大值取所有有效间隔最大','s','body[133-140]','M为评价区域内全部有效重访时间间隔的数量',dict(mean='sum(all gaps)/len(all gaps)',max='max(all gaps)'), '不是各点均值的均值；本轮分母仍为16样本点',supported)
    row('censoring_and_empty','首尾删失单列；无访问/不足两次保持分母；无gap为null','s','body[126-135],body[190]','连续两次有效访问；无法形成至少两次有效访问',dict(edges_excluded=True,no_gap=None,insufficient_retained=True),
        '原文未明确窗口首尾删失或无gap数值；条件均值不能掩盖未重访点',assumed)
    row('insufficient_revisit_score','部分重点区不足两次访问，重访项可直接判0','score','body[190]/paragraph','重访项可直接判0分',dict(official_score=None,engineering_h3='at least one stable internal gap'),
        '工程H3与官方重访合格不同；当前样本不能裁定全区，但旧7星13/16不足两次不应声称官方通过',supported)
    row('io_and_submission','输入1<N<=100，逐星输出h/i/RAAN/u；需28星完整工程与全球/区域分析','km,deg','body[23-47]','卫星总数量N：1<N≤100；卫星数量为28颗的星座完整工程仿真文件',dict(generator='src.constellation.generate',output='generate/orbits.csv',this_scope='28-star 16-point engineering only'),
        '精确接口文件schema未给；本轮没有官方全球/全区分析，不能称完整正式提交',supported)
    row('scoring_formula','28*T90*/T90+12*T100*/T100+42*Tavg*/Tavg+18*Tmax*/Tmax；各工况算术平均','score','body[103-112],body[158-174]',
        'SN=SC+SR；三个卫星规模权重相同',dict(official_score=None,internal_objective_is_not_score=True),
        '最优值来自本届有效方案；N=30/60/100为原文示例且可后续调整，未获最终补充',supported)
    row('invalid_output','参数越界、星数不符、不能建模传播、格式不符，该工况0分','score','body[191-198]','超出规定轨道参数范围；卫星数量与输入不一致；STK无法正常建立或传播',dict(validation='explicit frozen config, orbit/readback/propagation audits'),'官方接口格式仍需确认',supported)
    result=dict(status='PASS',critical_conflicts=[],official_readiness='UNRESOLVED',engineering_continuation_allowed=True,
        source_document=document['source'],source_sha256=document['sha256'],source_scope='Supplied official challenge DOCX; no verified organizer supplement discovered locally or supplied; no secondary summary promoted to official evidence',
        extraction=str(base/'official_document_extract.json'),rows=rows,official_metrics_or_score_produced=False,
        interpretation='No explicit conclusion-changing conflict found. Unspecified choices retain round6 engineering identity; official readiness remains incomplete.')
    write(out/'metrics_contract.json',result)
    text='# 第七轮评价合同核对\n\n正式口径就绪：UNRESOLVED。未发现阻塞同条件工程基线的明确关键冲突。所有工程假设保持第六轮；不产生官方全区指标或得分。\n\n'
    text+=f'原始DOCX：`{document["source"]}`\n\nSHA-256：`{document["sha256"]}`。定位使用OOXML正文序号、表格行列，未推测页码。已阅读段落、表格和嵌入的示例覆盖率图；图中2026年示例日期不替代2027年统一条件表。\n\n'
    text+='|项目|状态|定义/单位|官方定位与短依据|本机实现|差异/待确认|\n|---|---|---|---|---|---|\n'
    for r in rows:text+='|'+ '|'.join([r['id'],r['status'],r['definition']+' / '+r['unit'],r['official_source']['locator']+'：'+r['official_source']['short_quote'],str(r['implementation']).replace('|','/'),r['difference_or_open_question']])+'|\n'
    (out/'metrics_contract.md').write_text(text,encoding='utf-8');return result
