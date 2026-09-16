#!/usr/bin/env bash
# 把公开资料抓进私有知识库 D:\blog-knowledge\documents
#
#   bash D:/blog-swarm/tools/fetch_knowledge.sh            # 抓全部
#   bash D:/blog-swarm/tools/fetch_knowledge.sh hermes     # 只抓名字含 hermes 的
#
# 已存在且非空的文件会跳过；单个失败不影响其它文件。
set -u

DEST="${BLOG_RAG_SOURCE:-D:/blog-knowledge/documents}"
FILTER="${1:-}"

mkdir -p "$DEST"

# 名称|URL|说明
SOURCES='
hermes-docs-index|https://hermes-agent.nousresearch.com/docs/llms.txt|Hermes 全部文档的索引（每行一条 + 链接）
hermes-docs-full|https://hermes-agent.nousresearch.com/docs/llms-full.txt|Hermes 文档全集单文件（>3MB，慢，建议后台跑）
arxiv-rag-2005.11401|https://arxiv.org/pdf/2005.11401|RAG 原始论文（Lewis et al. 2021）
arxiv-eval-error-bars-2411.00640|https://arxiv.org/pdf/2411.00640|Anthropic：给评测加误差棒（Wilson/Jeffreys 区间）
arxiv-react-2210.03629|https://arxiv.org/pdf/2210.03629|ReAct：推理+行动交替
arxiv-ragas-2309.15217|https://arxiv.org/pdf/2309.15217|RAGAS：RAG 评测指标（忠实度/答案相关性）
arxiv-lost-in-the-middle-2307.03172|https://arxiv.org/pdf/2307.03172|长上下文检索位置偏差（分块/召回的依据）
arxiv-selfcheckgpt-2303.08896|https://arxiv.org/pdf/2303.08896|SelfCheckGPT：幻觉检测
arxiv-memgpt-2310.08560|https://arxiv.org/pdf/2310.08560|MemGPT：分层记忆管理
arxiv-autogen-2308.08155|https://arxiv.org/pdf/2308.08155|AutoGen：多智能体对话框架
'

ok=0; skip=0; fail=0
printf '%s\n' "$SOURCES" | while IFS='|' read -r name url note; do
  [ -z "${name:-}" ] && continue
  case "$name" in \#*) continue ;; esac
  if [ -n "$FILTER" ]; then
    case "$name" in *"$FILTER"*) ;; *) continue ;; esac
  fi
  ext=".pdf"
  case "$url" in *.txt) ext=".txt" ;; esac
  target="$DEST/$name$ext"

  if [ -s "$target" ]; then
    printf '  skip  %s（已存在，%s bytes）\n' "$(basename "$target")" "$(stat -c%s "$target")"
    continue
  fi

  printf '  get   %s ... ' "$(basename "$target")"
  if curl -sSL --fail --retry 2 --retry-delay 3 \
        --connect-timeout 20 --max-time 600 \
        -o "$target.part" "$url"; then
    if [ -s "$target.part" ]; then
      mv "$target.part" "$target"
      printf 'ok %s bytes\n' "$(stat -c%s "$target")"
    else
      rm -f "$target.part"; printf 'FAIL 空文件\n'
    fi
  else
    rm -f "$target.part"; printf 'FAIL 网络错误\n'
  fi
done

echo
echo "完成。目标目录：$DEST"
echo "下一步（增量索引，已有内容不会重复处理）："
echo "  D:/blog-swarm/.venv-rag/Scripts/python.exe -m private_rag.cli index \\"
echo "    --source $DEST --db D:/blog-knowledge/index --embedding hashing"
echo
echo "注意：'hermes-docs-full' 与两个大论文（MetaGPT/Generative Agents）体积大、下载慢，"
echo "      单个失败就重跑一次本脚本即可，已下好的会跳过。"
echo
echo "未加入默认列表的超大文件（各 12–17MB，需要时手工抓）："
echo "  MetaGPT            https://arxiv.org/pdf/2308.00352   (16.7MB)"
echo "  Generative Agents  https://arxiv.org/pdf/2304.03442   (11.9MB)"
