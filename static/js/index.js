document.addEventListener("DOMContentLoaded", function () {
    const dateInput = document.getElementById("dateInput");

    if (dateInput) {
        // 한국 시간(KST) 기준으로 오늘 날짜 계산
        const now = new Date();
        const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
        const kstTime = new Date(utc + (9 * 60 * 60000));

        // 날짜 포맷팅 함수 (YYYY-MM-DD)
        const formatDate = (date) => {
            const yyyy = date.getFullYear();
            const mm = String(date.getMonth() + 1).padStart(2, '0');
            const dd = String(date.getDate()).padStart(2, '0');
            return `${yyyy}-${mm}-${dd}`;
        };

        // 1. 최소 날짜 설정: 오늘로부터 3일 후
        const minDate = new Date(kstTime);
        minDate.setDate(kstTime.getDate() + 3);
        const minDateStr = formatDate(minDate);
        dateInput.setAttribute("min", minDateStr);

        // 2. 최대 날짜 설정: 오늘로부터 14일 후
        const maxDate = new Date(kstTime);
        maxDate.setDate(kstTime.getDate() + 14);
        const maxDateStr = formatDate(maxDate);
        dateInput.setAttribute("max", maxDateStr);

        // 현재 선택된 날짜가 범위를 벗어난 경우 비워주기 (선택 사항)
        if (dateInput.value && (dateInput.value < minDateStr || dateInput.value > maxDateStr)) {
            dateInput.value = ""; 
        }
    }
});