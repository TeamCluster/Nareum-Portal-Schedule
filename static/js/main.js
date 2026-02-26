document.addEventListener("DOMContentLoaded", function() {
    
    // 1. [index.html] 페이지 로드 시 날짜가 없으면 자동으로 오늘 날짜 세팅
    const dateInput = document.getElementById("dateInput");
    if (dateInput && !dateInput.value) {
        const today = new Date();
        const yyyy = today.getFullYear();
        const mm = String(today.getMonth() + 1).padStart(2, '0');
        const dd = String(today.getDate()).padStart(2, '0');
        // dateInput.value = `${yyyy}-${mm}-${dd}`; // 필요 시 주석 해제하여 사용
    }

    // 2. [reserve.html] 공지 동의 체크박스 상태에 따른 버튼 활성화 토글
    const agreeCheckbox = document.getElementById("agree");
    const submitBtn = document.getElementById("submitBtn");

    if (agreeCheckbox && submitBtn) {
        // 초기 로드 시 확인 (검증 실패로 돌아왔을 때 대비)
        submitBtn.disabled = !agreeCheckbox.checked;

        // 체크박스 변경 시 이벤트 리스너
        agreeCheckbox.addEventListener("change", function() {
            submitBtn.disabled = !agreeCheckbox.checked;
        });
    }
});