document.addEventListener("DOMContentLoaded", function () {

    // 1. [index.html] 한국 시간(KST) 기준으로 오늘 이전 날짜 선택 방지
    const dateInput = document.getElementById("dateInput");
    if (dateInput) {
        // 한국 시간 계산 (UTC+9)
        const now = new Date();
        const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
        const kstTime = new Date(utc + (9 * 60 * 60000));

        const yyyy = kstTime.getFullYear();
        const mm = String(kstTime.getMonth() + 1).padStart(2, '0');
        const dd = String(kstTime.getDate()).padStart(2, '0');

        const todayStr = `${yyyy}-${mm}-${dd}`;

        // min 속성 설정 (오늘 이전 날짜 비활성화)
        dateInput.setAttribute("min", todayStr);

        // 페이지 로드 시 날짜가 비어있으면 오늘 날짜로 자동 세팅 (선택 사항)
        if (!dateInput.value) {
            dateInput.value = todayStr; 
        }
    }

    // 2. [reserve.html] 공지 동의 체크박스 상태에 따른 버튼 활성화 토글
    const agreeCheckbox = document.getElementById("agree");
    const submitBtn = document.getElementById("submitBtn");

    if (agreeCheckbox && submitBtn) {
        submitBtn.disabled = !agreeCheckbox.checked;
        agreeCheckbox.addEventListener("change", function () {
            submitBtn.disabled = !agreeCheckbox.checked;
        });
    }
});