# GitHub仓库使用说明

仓库地址：https://github.com/estenbernhardpj109/xioaxiaoxianshengGZC

默认分支：main。应用源码直接放在仓库根目录，克隆后运行 `python backend/server.py --port 8011`。

源码目录为backend和frontend，data保存模拟数据，tests保存测试，docs保存技术文档、安全自评、答辩PPT和讲稿，demo保存演示视频。项目运行不需要安装第三方Python依赖。

本次仓库发布排除.runtime、日志、缓存、node_modules和实际.env文件。只保留不含密钥的.env.example。DeepSeek密钥由运行环境注入，不写入GitHub。

GitHub负责托管版本和评审材料，不直接运行Python银行后端。需要在线运行时，应另行部署到支持Python的服务环境，并落实安全自评列出的生产接入要求。未把银行功能伪装成仅靠GitHub Pages即可运行。

部分PDF、PPT及历史讲稿记录了材料制作时的本地交付状态。当前仓库发布情况以GitHub提交记录为准，赛事平台上传状态以平台记录为准。团队名称、成员与学校仍需团队确认填写。
