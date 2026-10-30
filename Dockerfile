# Bullseye uses glibc 2.31, compatible with Docker 19.03's clone3/seccomp behavior.
# Keep Python 3.12: application code requires asyncio.timeout (Python >= 3.11).
ARG PYTHON_IMAGE=python:3.12-slim-bullseye
FROM ${PYTHON_IMAGE}
LABEL description="按项目名称严格匹配材料清单，适配 HiAgent param 输入"
WORKDIR /home/app
COPY src/requirements.txt ./requirements.txt
ARG PIP_INDEX_URL=http://10.240.99.210:35001/simple
ARG PIP_TRUSTED_HOST=10.240.99.210
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}"
COPY src/*.py src/start.sh ./
COPY list/ ./list/
EXPOSE 8000
CMD ["sh", "start.sh"]
