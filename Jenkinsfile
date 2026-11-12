/* 本项目编码格式：UTF-8（无 BOM）。 */
/*
 * Jenkins 声明式流水线（P0）——与 GitHub Actions 三段门禁等价，但把测试结果做成「可发布的报告」。
 *
 * 为什么需要它：GitHub Actions 只有控制台文本时，CI 挂了得翻日志找用例；Jenkins 这边
 * junit 让构建页能按用例定位失败、recordCoverage 给覆盖率趋势、archiveArtifacts 留制品。
 * 报告从此是「页面 + 制品」两件事，同源（result_summary / JUnit XML）不同渲染。
 *
 * 依赖插件：JUnit、Coverage（Cobertura 解析器）、HTML Publisher。
 * 依赖工具：agent 具备 Python 3.12+；RUN_DOCKER 打开时还需 docker（含 compose 插件）。
 * 注意：本文件不参与 pytest 门禁，语法问题只能在 Jenkins 上暴露——首次接入请用「流水线语法」校验。
 */
pipeline {
  agent any

  parameters {
    string(name: 'PYTHON_BIN', defaultValue: 'python3', description: 'Python 3.12+ 可执行文件（用于创建 venv）')
    booleanParam(name: 'RUN_SLOW', defaultValue: true, description: '是否执行慢测试（e2e 冒烟，mock 隔离）')
    booleanParam(name: 'RUN_DOCKER', defaultValue: true, description: '是否构建 Docker 镜像（需 agent 具备 docker）')
  }

  options {
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '30'))
    disableConcurrentBuilds()
  }

  environment {
    // why：参数转环境变量后由 shell 展开（Groovy 三引号不插值），避免 CLI 引号嵌套问题
    PYTHON_BIN = "${params.PYTHON_BIN}"
  }

  stages {
    stage('准备环境') {
      steps {
        sh '''
          set -e
          if [ ! -d .venv ]; then "${PYTHON_BIN}" -m venv .venv; fi
          .venv/bin/pip install -e ".[dev]"
          rm -rf reports
          mkdir -p reports
        '''
      }
    }

    stage('静态检查 + 文档门禁') {
      steps {
        sh '''
          set -e
          .venv/bin/ruff check app tests scripts
          .venv/bin/ruff format --check .
          .venv/bin/python scripts/doc_check.py
          .venv/bin/python scripts/doc_hygiene.py
          .venv/bin/python scripts/encoding_check.py
        '''
      }
    }

    stage('测试与覆盖率') {
      steps {
        sh '''
          set -e
          .venv/bin/pytest --cov=app \
            --junitxml=reports/junit.xml \
            --alluredir=reports/allure-results --clean-alluredir \
            --cov-report=xml:reports/coverage.xml \
            --cov-report=html:reports/htmlcov \
            --cov-report=term-missing
          .venv/bin/coverage report --include='app/services/*.py,app/utils/*.py' --fail-under=80
        '''
      }
      post {
        always {
          // 失败时同样挂结果：构建页要能直接指到哪条用例红
          junit allowEmptyResults: true, testResults: 'reports/junit.xml'
          script {
            // 收集期崩溃（pytest 退出码 2/3/4）时 coverage.xml 可能不存在，
            // 无条件 recordCoverage 会把「测试失败」改写成「流水线脚本错误」，掩盖真实原因
            if (fileExists('reports/coverage.xml')) {
              recordCoverage tools: [[parser: 'COBERTURA', pattern: 'reports/coverage.xml']]
            }
          }
        }
      }
    }

    stage('慢测试（e2e 冒烟）') {
      when { expression { params.RUN_SLOW } }
      steps {
        sh '''
          set -e
          .venv/bin/pytest -m slow --junitxml=reports/junit-slow.xml
        '''
      }
      post {
        always {
          junit allowEmptyResults: true, testResults: 'reports/junit-slow.xml'
        }
      }
    }

    stage('镜像构建验证') {
      when { expression { params.RUN_DOCKER } }
      steps {
        sh '''
          set -e
          docker compose config
          docker build -t testplatform:ci .
        '''
      }
    }
  }

  post {
    always {
      script {
        // Allure 报告：需 Jenkins 配置 Allure 全局工具（Manage Jenkins → Tools → Allure），
        // 安装名与下方 commandline 一致；未配置时该步骤会失败。
        // why try/catch：报告是展示层，未配工具不该让已通过的测试门禁判红——只记日志并继续。
        // why 显式传 commandline：Allure 3.x 插件不指定安装名会直接抛 "No Allure installation found"。
        if (fileExists('reports/allure-results')) {
          try {
            allure includeProperties: false,
                   jdk: '',
                   commandline: 'allure-cli',
                   results: [[path: 'reports/allure-results']]
          } catch (err) {
            echo "Allure 报告生成跳过（未配置 Allure 全局工具或 CLI 不可用）: ${err}"
          }
        }
      }
      archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
      script {
        if (fileExists('reports/htmlcov/index.html')) {
          publishHTML target: [
            reportDir: 'reports/htmlcov',
            reportFiles: 'index.html',
            reportName: 'Coverage HTML',
            keepAll: true
          ]
        }
      }
    }
  }
}
