run "isolated_defaults" {
  command = plan
  assert {
    condition     = terraform_data.cluster.input.config.cluster_name == "production-demo-gitops"
    error_message = "The default must not target the legacy cluster."
  }
  assert {
    condition     = !terraform_data.cluster.input.config.allow_github_writes
    error_message = "External writes must require explicit authorization."
  }
  assert {
    condition     = output.application_url == "http://demo.localhost:18080"
    error_message = "Use the isolated application port."
  }
}

run "reject_legacy_cluster" {
  command = plan
  variables {
    cluster_name = "production-demo"
  }
  expect_failures = [var.cluster_name]
}

run "reject_legacy_ports" {
  command = plan
  variables {
    http_port = 8080
  }
  expect_failures = [terraform_data.preflight]
}

run "reject_source_writeback_loop" {
  command = plan
  variables {
    source_branch = "gitops/local"
  }
  expect_failures = [terraform_data.preflight]
}
