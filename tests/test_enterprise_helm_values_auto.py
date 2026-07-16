from copy import deepcopy
from pathlib import Path
import sys,time,tempfile
ROOT=Path.cwd(); sys.path.insert(0,str(ROOT))
from src.yaml_config_engine.diff_compiler import DiffCompiler
from src.yaml_config_engine.engine import YamlPatchEngine
from src.yaml_config_engine.comparison import strict_equal
from src.yaml_config_engine.yamlio import dumps

APPS=['gateway','order-api','scheduler']
ENVS=['stg','prod']
REGIONS=['north','south']
FABS=['FAB14','FAB18']
WORKLOADS=['api','worker']

def container(name, app, workload, env):
    return {
      'name':name,
      'image': {'repository':f'registry.local/{app}/{name}','tag':'2025.12.1','pullPolicy':'IfNotPresent'},
      'ports':[{'name':'http','containerPort':8080,'protocol':'TCP'},{'name':'metrics','containerPort':9090,'protocol':'TCP'}],
      'env':[{'name':'APP_ENV','value':env},{'name':'LOG_LEVEL','value':'INFO'},{'name':'FEATURE_FLAGS','valueFrom':{'configMapKeyRef':{'name':f'{app}-flags','key':workload}}}],
      'resources':{'requests':{'cpu':'250m','memory':'256Mi'},'limits':{'cpu':'1','memory':'1Gi'}},
      'securityContext':{'runAsNonRoot':True,'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
      'livenessProbe':{'httpGet':{'path':'/health/live','port':'http'},'initialDelaySeconds':20,'periodSeconds':10},
      'readinessProbe':{'httpGet':{'path':'/health/ready','port':'http'},'initialDelaySeconds':10,'periodSeconds':5},
      'volumeMounts':[{'name':'config','mountPath':'/etc/app','readOnly':True},{'name':'tmp','mountPath':'/tmp'}],
    }

def workload(app, w, env, fab, region):
    main=container('main',app,w,env)
    side=container('log-agent',app,w,env)
    side['image']['repository']='registry.local/platform/log-agent'
    side['ports']=[]
    return {
      'enabled':True,
      'replicaCount':2 if env=='prod' else 1,
      'deployment':{'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxUnavailable':0,'maxSurge':1}},'revisionHistoryLimit':5,'progressDeadlineSeconds':600},
      'podAnnotations':{'prometheus.io/scrape':'true','prometheus.io/port':'9090'},
      'podLabels':{'app.kubernetes.io/part-of':'fab-platform','topology.region':region,'topology.fab':fab},
      'serviceAccount':{'create':True,'automount':False,'annotations':{}},
      'podSecurityContext':{'runAsUser':10001,'runAsGroup':10001,'fsGroup':10001,'seccompProfile':{'type':'RuntimeDefault'}},
      'containers':[main,side],
      'initContainers':[{'name':'wait-config','image':{'repository':'registry.local/platform/busybox','tag':'1.36'},'command':['sh','-c','test -f /config/ready'],'volumeMounts':[{'name':'config','mountPath':'/config'}]}],
      'service':{'enabled':True,'type':'ClusterIP','ports':[{'name':'http','port':80,'targetPort':'http'},{'name':'metrics','port':9090,'targetPort':'metrics'}]},
      'ingress':{'enabled':w=='api','className':'nginx','annotations':{'nginx.ingress.kubernetes.io/proxy-body-size':'10m'},'hosts':[{'host':f'{app}-{env}-{fab.lower()}-{region}.internal','paths':[{'path':'/','pathType':'Prefix','servicePort':'http'}]}],'tls':[]},
      'autoscaling':{'enabled':w!='cron','minReplicas':2 if env=='prod' else 1,'maxReplicas':10,'targetCPUUtilizationPercentage':70,'targetMemoryUtilizationPercentage':75,'behavior':{'scaleUp':{'stabilizationWindowSeconds':0,'policies':[{'type':'Percent','value':100,'periodSeconds':60},{'type':'Pods','value':4,'periodSeconds':60}]},'scaleDown':{'stabilizationWindowSeconds':300,'policies':[{'type':'Percent','value':25,'periodSeconds':60}]}}},
      'pdb':{'enabled':env=='prod','minAvailable':1},
      'nodeSelector':{'kubernetes.io/os':'linux','fab':fab.lower(),'region':region},
      'tolerations':[{'key':'workload','operator':'Equal','value':'application','effect':'NoSchedule'}],
      'affinity':{'podAntiAffinity':{'preferredDuringSchedulingIgnoredDuringExecution':[{'weight':100,'podAffinityTerm':{'topologyKey':'kubernetes.io/hostname','labelSelector':{'matchExpressions':[{'key':'app.kubernetes.io/name','operator':'In','values':[app]}]}}}]},'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'node-pool','operator':'In','values':['general','compute']}]}]}}},
      'topologySpreadConstraints':[{'maxSkew':1,'topologyKey':'topology.kubernetes.io/zone','whenUnsatisfiable':'ScheduleAnyway','labelSelector':{'matchLabels':{'app.kubernetes.io/name':app}}}],
      'volumes':[{'name':'config','configMap':{'name':f'{app}-config'}},{'name':'tmp','emptyDir':{'sizeLimit':'1Gi'}},{'name':'certs','secret':{'secretName':f'{app}-tls'}}],
      'config':{'database':{'host':f'{app}-db','port':5432,'pool':{'min':2,'max':20,'timeoutSeconds':30}},'kafka':{'brokers':['kafka-0:9092','kafka-1:9092'],'consumer':{'groupId':f'{app}-{w}','maxPollRecords':500}},'features':{'audit':True,'tracing':True,'experimental':False}},
      'observability':{'serviceMonitor':{'enabled':True,'interval':'30s','scrapeTimeout':'10s','labels':{'release':'monitoring'}},'logging':{'format':'json','level':'INFO'},'tracing':{'enabled':True,'samplingRate':0.1}},
    }

def build():
  root={'global':{'clusterDomain':'cluster.local','imagePullSecrets':[{'name':'registry-secret'}],'commonLabels':{'managed-by':'helm','team':'platform'},'commonAnnotations':{'config/version':'v1'},'networkPolicy':{'enabled':True,'defaultDeny':True}},'environments':{}}
  for env in ENVS:
    e={'regions':{}}
    for region in REGIONS:
      r={'fabs':{}}
      for fab in FABS:
        f={'apps':{}}
        for app in APPS:
          f['apps'][app]={'metadata':{'owner':f'team-{app}','tier':'backend','criticality':'high' if env=='prod' else 'normal'},'workloads':{w:workload(app,w,env,fab,region) for w in WORKLOADS},'externalSecrets':[{'name':'database','refreshInterval':'1h','data':[{'secretKey':'username','remoteRef':{'key':f'/{env}/{fab}/{app}/db','property':'username'}},{'secretKey':'password','remoteRef':{'key':f'/{env}/{fab}/{app}/db','property':'password'}}]}]}
        r['fabs'][fab]=f
      e['regions'][region]=r
    root['environments'][env]=e
  return root

def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    before = build()
    after = deepcopy(before)
    # Broad common updates across deep dict/list structures.
    for env in ENVS:
      for region in REGIONS:
        for fab in FABS:
          for app in APPS:
            for w in WORKLOADS:
              obj=after['environments'][env]['regions'][region]['fabs'][fab]['apps'][app]['workloads'][w]
              obj['autoscaling']['maxReplicas']=20
              obj['observability']['logging']['level']='WARN'
              obj['deployment']['progressDeadlineSeconds']=900
              for c in obj['containers']:
                c['resources']['requests']['cpu']='500m'
                c['securityContext']['readOnlyRootFilesystem']=False
              obj['containers'][0]['image']['tag']='2026.01.0'
    # Environment-specific common updates.
    for region in REGIONS:
      for fab in FABS:
        for app in APPS:
          for w in WORKLOADS:
            o=after['environments']['prod']['regions'][region]['fabs'][fab]['apps'][app]['workloads'][w]
            o['replicaCount']=3
            o['autoscaling']['minReplicas']=3
    # App/workload-specific residuals that must not be over-generalized.
    for env in ENVS:
      for region in REGIONS:
        for fab in FABS:
          after['environments'][env]['regions'][region]['fabs'][fab]['apps']['gateway']['workloads']['api']['ingress']['annotations']['nginx.ingress.kubernetes.io/proxy-body-size']='50m'
          after['environments'][env]['regions'][region]['fabs'][fab]['apps']['scheduler']['workloads']['worker']['config']['features']['experimental']=True

    line_count = len(dumps(before).splitlines())
    require(line_count >= 10000, f'enterprise Helm fixture must exceed 10,000 lines, got {line_count}')
    compiler = DiffCompiler(optimization_timeout_seconds=8, optimization_max_candidates=120)
    started = time.monotonic()
    result = compiler.compile(before, after)
    elapsed = time.monotonic() - started
    require(result.verified, 'enterprise Helm auto compile must verify')
    operations = result.config['operations']
    require(len(operations) <= 10, f'expected <=10 merged operations, got {len(operations)}')
    rendered = repr(operations)
    require("$/environments/*/regions/*/fabs/*/apps/*/workloads/*" in rendered, 'missing broad Helm wildcard collapse')
    require("apps/gateway/workloads/api" in rendered, 'gateway-specific residual must remain explicit')
    require("apps/scheduler/workloads/worker" in rendered, 'scheduler-specific residual must remain explicit')
    require("$/environments/prod/regions/*/fabs/*/apps/*/workloads/*" in rendered, 'prod-only changes must remain scoped')
    applied = YamlPatchEngine().apply_document(deepcopy(before), result.config, track_no_effect=False)
    require(strict_equal(applied, after), 'enterprise Helm merged config must replay exactly')
    print(f'PASS: enterprise Helm values auto lines={line_count}, operations={len(operations)}, elapsed={elapsed:.3f}s')


if __name__ == '__main__':
    main()
