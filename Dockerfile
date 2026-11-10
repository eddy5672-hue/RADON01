# (선택) Render 외 클라우드·서버에 컨테이너로 배포할 때 사용
FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PORT=8080
EXPOSE 8080
CMD ["python", "run.py", "--no-browser"]
