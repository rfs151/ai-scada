// ai-scada CI on the on-demand Jenkins that lives in the ai-vision repo (ai-vision/ci/jenkins).
// Same checks as .github/workflows/ci.yml (ruff → pytest per package → web test/build), no image push or deploy.
pipeline {
  agent any
  options {
    timestamps()
    ansiColor('xterm')
    timeout(time: 30, unit: 'MINUTES')
    disableConcurrentBuilds()
  }
  environment {
    VENV = "${env.JENKINS_HOME}/cache/venv-ai-scada"   // shared across builds: pip only reinstalls when requirements change
    NPM_CONFIG_CACHE = "${env.JENKINS_HOME}/cache/npm"
    PY_SUITES = 'packages/scadalib services/rtu-sim services/fep services/api'
  }
  stages {
    stage('准备环境') {
      steps {
        sh '''
          set -e
          HASH=$(cat requirements-dev.txt services/*/requirements.txt | sha1sum | cut -c1-12)
          if [ ! -f "$VENV/.req-$HASH" ]; then
            rm -rf "$VENV" && python3 -m venv "$VENV"
            "$VENV/bin/pip" install -q ${PIP_INDEX_URL:+-i $PIP_INDEX_URL} -r requirements-dev.txt
            touch "$VENV/.req-$HASH"
          fi
        '''
      }
    }
    stage('代码检查') {
      steps {
        sh '"$VENV/bin/ruff" check packages services && "$VENV/bin/ruff" format --check packages services'
      }
    }
    stage('单元测试') {
      steps {
        sh '''
          set -e
          mkdir -p reports
          for d in $PY_SUITES; do
            echo "== $d"
            (cd "$d" && "$VENV/bin/python" -m pytest -q --junitxml="$WORKSPACE/reports/$(basename "$d").xml")
          done
        '''
      }
      post { always { junit allowEmptyResults: true, testResults: 'reports/*.xml' } }
    }
    stage('前端') {
      steps {
        dir('web') {
          sh 'npm ci --no-audit --no-fund && npm test && npm run build'
        }
        archiveArtifacts artifacts: 'web/dist/**', fingerprint: true
      }
    }
  }
  post {
    success { echo "✅ ai-scada ${(env.GIT_COMMIT ?: "local").take(7)} 通过" }
    failure { echo "❌ 构建失败，见上方日志" }
  }
}
