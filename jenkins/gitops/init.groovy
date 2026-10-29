import jenkins.model.Jenkins
import hudson.plugins.git.GitSCM
import hudson.plugins.git.UserRemoteConfig
import hudson.plugins.git.BranchSpec
import hudson.triggers.SCMTrigger
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import org.jenkinsci.plugins.workflow.job.properties.PipelineTriggersJobProperty

def ssh = new File(Jenkins.get().rootDir, '.ssh')
ssh.mkdirs()
new File(ssh, 'known_hosts').text = new File('/run/secrets/github_known_hosts').text
def job = Jenkins.get().getItem('production-demo')
if (job == null) {
    job = Jenkins.get().createProject(WorkflowJob, 'production-demo')
}
def scm = new GitSCM(
    [new UserRemoteConfig(System.getenv('DEMO_SOURCE_URL'), null, null, 'github-read-only')],
    [new BranchSpec('*/' + System.getenv('DEMO_SOURCE_BRANCH'))], false, [], null, null, []
)
def definition = new CpsScmFlowDefinition(scm, 'Jenkinsfile.gitops')
definition.lightweight = true
job.definition = definition
job.description = 'GitOps CI: builds images and publishes desired state; Argo CD owns deployment.'
job.removeProperty(PipelineTriggersJobProperty)
job.addProperty(new PipelineTriggersJobProperty([new SCMTrigger('H/2 * * * *')]))
job.save()
