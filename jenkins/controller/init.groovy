import jenkins.model.Jenkins
import hudson.plugins.git.GitSCM
import hudson.plugins.git.UserRemoteConfig
import hudson.plugins.git.BranchSpec
import hudson.triggers.SCMTrigger
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import org.jenkinsci.plugins.workflow.job.properties.PipelineTriggersJobProperty

def home = Jenkins.get().rootDir
def ssh = new File(home, '.ssh')
ssh.mkdirs()
new File(ssh, 'known_hosts').text = new File('/run/secrets/github_known_hosts').text
def job = Jenkins.get().getItem('production-demo')
if (job == null) {
    job = Jenkins.get().createProject(WorkflowJob, 'production-demo')
}
def scm = new GitSCM(
    [new UserRemoteConfig('git@github.com:kovokilla/kubernetes-production-demo.git', null, null, 'github-read-only')],
    [new BranchSpec('*/main')], false, [], null, null, []
)
def definition = new CpsScmFlowDefinition(scm, 'Jenkinsfile')
definition.lightweight = true
job.definition = definition
job.description = 'Trusted main only. Local Docker access; do not enable untrusted PR builds.'
job.removeProperty(PipelineTriggersJobProperty)
job.addProperty(new PipelineTriggersJobProperty([new SCMTrigger('H/2 * * * *')]))
job.save()
