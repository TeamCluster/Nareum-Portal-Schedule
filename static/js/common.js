// 전화번호 자동 하이픈 및 정규화 공통 함수
function formatPhoneNumber(input) {
    let num = input.value.replace(/[^0-9]/g, '');
    let res = '';
    
    if(num.startsWith('02')) {
        if(num.length < 3) res = num;
        else if(num.length < 6) res = num.substr(0,2) + '-' + num.substr(2);
        else if(num.length < 10) res = num.substr(0,2) + '-' + num.substr(2,3) + '-' + num.substr(5);
        else res = num.substr(0,2) + '-' + num.substr(2,4) + '-' + num.substr(6,4);
    } else if (num.startsWith('1')) { 
        if(num.length < 5) res = num;
        else if(num.length < 9) res = num.substr(0,4) + '-' + num.substr(4);
        else res = num.substr(0,4) + '-' + num.substr(4,4);
    } else { 
        if(num.length < 4) res = num;
        else if(num.length < 7) res = num.substr(0,3) + '-' + num.substr(3);
        else if(num.length < 11) res = num.substr(0,3) + '-' + num.substr(3,3) + '-' + num.substr(6);
        else res = num.substr(0,3) + '-' + num.substr(3,4) + '-' + num.substr(7,4);
    }
    
    input.value = res;
}