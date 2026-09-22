function check() {
    var checkBox = document.getElementById("checbox");
    var text1 = document.getElementsByClassName("text1");
    var text2 = document.getElementsByClassName("text2");
    var list1 = document.getElementsByClassName("list1");
    var list2 = document.getElementsByClassName("list2");
  
    for (var i = 0; i < text1.length; i++) {
      if (checkBox.checked == true) {
        text1[i].style.display = "block";
        text2[i].style.display = "none";
        list1[i].style.display = "none";
        list2[i].style.display = "block";        
      } else if (checkBox.checked == false) {
        text1[i].style.display = "none";
        text2[i].style.display = "block";
        list1[i].style.display = "block";
        list2[i].style.display = "none";        
      }
    }
  }
  check();
  